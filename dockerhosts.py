#!/usr/bin/python3

"""
dockerhosts service implementation
"""

from subprocess import Popen
from subprocess import PIPE
import threading
import json
import os
import shutil

import signal
import time

from docker import DockerClient, APIClient


DOCKERHOSTS_CONF_JSON = "/etc/dockerhosts.conf.json"

class DockerHostsService:
    """Service implementation"""

    class Config:
        """Service configuration"""
        def __init__(self):
            self.hosts_folder: str
            self.docker_executable: str
            self.dnsmasq_executable: str
            self.dnsmasq_parameters: list

            conf_data: dict

            if os.path.exists(DOCKERHOSTS_CONF_JSON):
                with open(DOCKERHOSTS_CONF_JSON, "r") as stream:
                    conf_data = json.load(stream)
            else:
                conf_data = dict()

            self.hosts_folder = conf_data.get("hosts-folder", "/var/run/docker-hosts")
            self.docker_executable = conf_data.get("docker-executable", "/usr/bin/docker")
            self.dnsmasq_executable = conf_data.get("dnsmasq-executable", "/usr/sbin/dnsmasq")
            self.dnsmasq_parameters = conf_data.get("dnsmasq-parameters", [
                "--no-daemon",
                "--clear-on-reload",
                "--no-resolv",
                "--no-hosts",
                "--listen-address=127.0.0.54",
                "--port=53"
            ])

    def __init__(self):
        self.config = DockerHostsService.Config()

        self.dnsmasq_process: Popen
        self.dnsmasq_process = None

        self.containers_thread: threading.Thread
        self.containers_thread = None
        self.stopping = False

        self.previous_hostinfo = {}
        # FIXME the base url ought to be configurable I suppose
        self.client = DockerClient(base_url='unix://var/run/docker.sock')
        self.api_client = APIClient(base_url='unix://var/run/docker.sock')

        if not os.path.exists(self.config.hosts_folder):
            os.makedirs(self.config.hosts_folder)

        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

    def reload_dnsmasq(self):
        """Force dnsmasq to reload. We hope. This is needed
        whenever docker removes a container, so that the stale
        dns record is not served forever. See e.g.
        https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=798653"""
        if (self.dnsmasq_process is not None) and (self.dnsmasq_process.poll() is not None):
            print("Reloading dnsmasq.")
            self.dnsmasq_process.send_signal(signal.SIGHUP)

    def stop(self, _signum, _frame):
        """Stops threads and cleanup resources"""
        print("Stop signal received.")
        self.stopping = True

        # Stop dnsmasq thread
        if (self.dnsmasq_process is not None) and (self.dnsmasq_process.poll() is not None):
            print("Stopping dnsmasq.")
            self.dnsmasq_process.send_signal(signal.SIGKILL)
            self.dnsmasq_process.kill()
            self.dnsmasq_process.wait()
            print("Dnsmasq exited.")

        # Stop containers listener
        if not self.containers_thread is None:
            self.containers_thread.join()

        # Remove temporary folder
        if os.path.exists(self.config.hosts_folder):
            shutil.rmtree(self.config.hosts_folder)

    def start(self):
        """Run service"""
        # Start containers thread
        self.containers_thread = threading.Thread(target=self.update_hosts_file)
        self.containers_thread.start()

        # Start dnsmasq process
        self.dnsmasq_process = self.start_dnsmasq()

        try:
            self.dnsmasq_process.wait()
        except KeyboardInterrupt:
            self.stop(None, None)

    def start_dnsmasq(self) -> Popen:
        """ Start dnsmasq process """
        args = []
        args.append(self.config.dnsmasq_executable)
        args.extend(self.config.dnsmasq_parameters)
        args.append("--hostsdir=%s" % self.config.hosts_folder)

        process = Popen(shell=True, stdout=PIPE, args=" ".join(args))
        return process

    def get_running_containers(self) -> list:
        """ Returns list of running containers """
        container_ids: list

        # get ids of running containers only, on all networks
        entries = self.client.containers.list()
        container_ids = [entry.short_id for entry in entries]
        container_ids.sort()
        return container_ids

    def inspect_container(self, id_to_inspect: list) -> list:
        """ Returns container information """
        container_details = self.api_client.inspect_container(id_to_inspect)
        return container_details

    def get_container_names_addr(self, c_id) -> list:
        """for a given container id, return a list with the ip addr and the hostname
        and alias, as well as the fqdn hostname and alias if they are different"""
        container_data = self.inspect_container(c_id)

        hostname = container_data["Config"]["Hostname"]
        hostname_fqdn = None
        if container_data["Config"]["Domainname"]:
            hostname_fqdn = hostname + "." + container_data["Config"]["Domainname"]
        alias = container_data["Name"].lstrip("/")
        alias_fqdn = None
        if container_data["Config"]["Domainname"]:
            alias_fqdn = alias + "." + container_data["Config"]["Domainname"]

        networks = list(container_data["NetworkSettings"]["Networks"].values())
        hostaddr = networks[0]["IPAddress"]

        names_addr = [hostaddr, hostname, alias]
        if hostname_fqdn:
            names_addr.append(hostname_fqdn)
        if alias_fqdn:
            names_addr.append(alias_fqdn)
        return names_addr

    def wait_must_exit(self, seconds=2) -> bool:
        """sleep the designated time and return True
        if we should exit after, False otherwise"""
        for _ in range(1, seconds+10):
            time.sleep(0.1)
            if self.stopping:
                return True
        return False

    def update_hosts_file(self):
        """Writes hosts file into temporary folder"""
        while not self.stopping:
            try:
                container_ids = self.get_running_containers()
            except ConnectionRefusedError:
                # no docker process? check again in a minute
                if self.wait_must_exit(60):
                    break

            if sorted(self.previous_hostinfo.keys()) != container_ids:
                ids_to_check = [c_id for c_id in container_ids
                                if c_id not in self.previous_hostinfo]

                filename = self.config.hosts_folder + "/hosts"
                entries = {}

                # do we need to reload dnsmaq? we'll see
                reload_required = False

                if ids_to_check:
                    # FIXME this should be logged at level INFO
                    # print("Running containers: " + " ".join(container_ids))

                    # inspect all running containers for which we do not
                    # already have info, collect hostname and ip addr
                    try:
                        for c_id in ids_to_check:
                            entries[c_id] = self.get_container_names_addr(c_id)
                    except ConnectionRefusedError:
                        # docker process went away? start over, try again in a minute
                        if self.wait_must_exit(60):
                            break

                    # collect host name, alias and ip addr from old info for containers
                    # still running
                    for c_id in self.previous_hostinfo:
                        if c_id in container_ids:
                            entries[c_id] = self.previous_hostinfo[c_id]

                    # if there were containers running that aren't now,
                    # we must reload dnsmasq so it tosses those entries,
                    # once the new file is written out
                    for c_id in self.previous_hostinfo:
                        if c_id not in container_ids:
                            reload_required = True

                # convert host info to lines of text for dnsmasq file
                lines = ["\t".join(entries[c_id]) for c_id in entries]

                with open(filename, 'w') as the_file:
                    header = "#  " + filename + "\n"
                    the_file.write(header)
                    the_file.write("\n".join(lines) + "\n")
                self.previous_hostinfo = entries

                if reload_required:
                    self.reload_dnsmasq()

            if self.wait_must_exit():
                break


def main():
    """Main method"""
    service = DockerHostsService()
    service.start()


main()
