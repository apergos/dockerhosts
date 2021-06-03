#!/usr/bin/python3

"""
dockerhosts service implementation
"""

from subprocess import Popen
from subprocess import PIPE
import threading
import json
import logging
import os
import sys
import shutil
import signal
import time

from docker import DockerClient, APIClient
from docker.errors import DockerException


DOCKERHOSTS_CONF_JSON = "/etc/dockerhosts.conf.json"


class DockerHostsService:
    """Service implementation"""

    class Config:
        """Service configuration"""
        def __init__(self):
            self.hosts_folder: str
            self.dnsmasq_executable: str
            self.dnsmasq_parameters: list
            self.no_docker_wait: int
            self.between_updates_wait: int

            conf_data: dict

            if os.path.exists(DOCKERHOSTS_CONF_JSON):
                with open(DOCKERHOSTS_CONF_JSON, "r") as stream:
                    conf_data = json.load(stream)
            else:
                conf_data = dict()

            self.hosts_folder = conf_data.get("hosts-folder", "/var/run/docker-hosts")
            self.no_docker_wait = conf_data.get("no-docker-wait", 60)
            self.between_updates_wait = conf_data.get("between-updates-wait", 2)
            self.docker_socket = conf_data.get("docker-socket", "unix://var/run/docker.sock")
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

        self.client = None
        self.api_client = None
        self.setup_docker_session()

        if not os.path.exists(self.config.hosts_folder):
            os.makedirs(self.config.hosts_folder)

        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

    def setup_docker_session(self):
        """initialize the docker client and api client sessions,
        bailing if we receive sigkill"""
        while not self.client or not self.api_client:
            try:
                if not self.client:
                    self.client = DockerClient(base_url=self.config.docker_socket)
                if not self.api_client:
                    self.api_client = APIClient(base_url=self.config.docker_socket)
            except DockerException:
                if self.wait_must_exit(self.config.no_docker_wait):
                    sys.exit(1)

    def reload_dnsmasq(self):
        """Force dnsmasq to reload. We hope. This is needed whenever docker removes
        a container, so that the stale dns record is not served forever. See e.g.
        https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=798653"""
        if self.dnsmasq_process is not None:
            logging.warning("Reloading dnsmasq.")
            self.dnsmasq_process.send_signal(signal.SIGHUP)

    def stop(self, _signum, _frame):
        """Stops threads and cleanup resources"""
        logging.warning("Stop signal received.")
        self.stopping = True

        # Stop dnsmasq thread
        if (self.dnsmasq_process is not None) and (self.dnsmasq_process.poll() is not None):
            logging.warning("Stopping dnsmasq.")
            self.dnsmasq_process.send_signal(signal.SIGKILL)
            self.dnsmasq_process.kill()
            self.dnsmasq_process.wait()
            logging.warning("Dnsmasq exited.")

        # Stop containers listener
        if self.containers_thread is not None:
            self.containers_thread.join()

        # Remove temporary folder
        if os.path.exists(self.config.hosts_folder):
            shutil.rmtree(self.config.hosts_folder)

    def start(self):
        """Run service"""
        # Start containers thread
        self.containers_thread = threading.Thread(target=self.update_hosts_file_as_needed)
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

    def inspect_container(self, id_to_inspect) -> list:
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
        if we should exit afterwards, False otherwise"""
        for _ in range(1, seconds+10):
            time.sleep(0.1)
            if self.stopping:
                return True
        return False

    def some_containers_missing(self, current_container_ids: list) -> bool:
        '''if there are containers that were running
        previously and now are missing, return True,
        otherwise False'''
        for c_id in self.previous_hostinfo:
            if c_id not in current_container_ids:
                return True
        return False

    @staticmethod
    def entries_to_lines(entries: dict) -> list:
        """convert container info entries to lines to be written
        to the hosts files
        each entry starts with the address followed by a number of
        hostnames"""
        lines = []
        for c_id in entries:
            # keep the address
            will_write = [entries[c_id][0]]
            # keep everything but single label names
            will_write.extend([field for field in entries[c_id][1:] if '.' in field])
            # there are only single label names. write the line as a comment
            line = "\t".join(will_write)
            if len(will_write) == 1:
                line = "# " + line
            lines.append(line)
        return lines

    def write_hostsfile(self, entries: dict):
        """convert entries into lines of text
        and write them to the hosts file"""
        lines = self.entries_to_lines(entries)
        filename = self.config.hosts_folder + "/hosts"
        with open(filename, 'w') as hostsfile:
            header = "#  " + filename + "\n"
            hostsfile.write(header)
            hostsfile.write("\n".join(lines) + "\n")

    def do_hostsfile_update(self, container_ids: list):
        """Write new hosts file"""
        ids_to_check = [c_id for c_id in container_ids
                        if c_id not in self.previous_hostinfo]

        entries = {}

        if not ids_to_check:
            return

        logging.debug("Running containers: %s", " ".join(container_ids))

        # inspect all newly running containers, collect hostname and ip addr
        try:
            for c_id in ids_to_check:
                entries[c_id] = self.get_container_names_addr(c_id)
        except ConnectionRefusedError:
            # docker process went away? start over, try again in a bit
            if self.wait_must_exit(self.config.no_docker_wait):
                return

        # for containers that were already running last check,
        # get host name, alias and ip addr from cached info
        for c_id in self.previous_hostinfo:
            if c_id in container_ids:
                entries[c_id] = self.previous_hostinfo[c_id]

        self.write_hostsfile(entries)

        # if some containers are no longer running, we must reload
        # dnsmasq so it clears its cache
        if self.some_containers_missing(container_ids):
            self.reload_dnsmasq()

        self.previous_hostinfo = entries

    def update_hosts_file_as_needed(self):
        """Writes hosts file into temporary folder with new contents, when
        list of running containers changes"""

        # we'll assume that docker is up and running to start with
        docker_running = True

        while not self.stopping:
            try:
                container_ids = self.get_running_containers()
            except IOError:
                # docker process is gone, clear the file and our cache of entries
                # if we didn't already do so
                if docker_running:
                    docker_running = False
                    self.write_hostsfile({})
                    self.reload_dnsmasq()
                    self.previous_hostinfo = {}
                # and wait a bit before the next check
                if self.wait_must_exit(self.config.no_docker_wait):
                    break
                continue

            docker_running = True
            if sorted(self.previous_hostinfo.keys()) != container_ids:
                self.do_hostsfile_update(container_ids)

            if self.wait_must_exit(self.config.between_updates_wait):
                break


def main():
    """Main method"""
    service = DockerHostsService()
    service.start()


main()
