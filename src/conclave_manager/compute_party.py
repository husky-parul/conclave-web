import os
import ast
import pystache

from kubernetes import client as k_client
from kubernetes import config as k_config
from kubernetes.client.rest import ApiException
from base64 import b64encode


class ComputeParty:
    """
    Represents one party in a computation.
    Generates all Kubernetes objects.
    """

    def __init__(self, pid, all_pids, timestamp, protocol, app, swift_data, jiff_server_ip):

        self.pid = pid
        self.all_pids = all_pids
        self.timestamp = timestamp
        self.app = app
        self.swift_data = swift_data
        self.jiff_server_ip = jiff_server_ip

        self.template_directory = "{}/templates/".format(os.path.dirname(os.path.realpath(__file__)))
        self.name = "conclave-{0}-{1}".format(timestamp, str(pid))
        self.config_map_name = "conclave-{0}-{1}-map".format(timestamp, str(pid))

        self.protocol = b64encode(protocol.encode()).decode()
        self.conclave_config = self.gen_conclave_config()
        self.config_map_body = self.define_config_map()
        self.pod_body = self.define_pod()
        self.service_body = self.define_service()

    def gen_swift_conf(self):
        """
        Loads Swift config using my credentials mounted on the pod via a ConfigMap.
        Will obviously need to be generalized in the future.
        """

        params = dict()

        params["auth_url"] = open("/etc/config/auth_url", "r").read()
        params["proj_domain"] = open("/etc/config/proj_domain", "r").read()
        params["proj_name"] = open("/etc/config/proj_name", "r").read()
        params["user_name"] = open("/etc/config/user_name", "r").read()
        params["pass"] = open("/etc/config/pass", "r").read()

        swift_str = ""
        container_name = ""

        # NOTE -- this assumes one input file per party and will need to be generalized
        if self.swift_data is not None:
            swift_str += "      - {}\n".format(self.swift_data["dataset"])
            # assumes a single container for all input files
            container_name = self.swift_data["container"]

        params["swift_data_str"] = swift_str
        params["container_name"] = container_name

        return params

    def gen_conclave_config(self):
        """
        Generate CC Config yaml.
        """

        net_str = self.gen_net_config()
        swift_params = self.gen_swift_conf()

        params = \
            {
                "PID": self.pid,
                "ALL_PIDS": ", ".join(str(i) for i in self.all_pids),
                "WORKFLOW_NAME": "conclave-{}".format(self.timestamp),
                "NET_CONFIG": net_str,
                "OS_AUTH": swift_params["auth_url"],
                "USERNAME": swift_params["user_name"],
                "PASSWORD": swift_params["pass"],
                "PROJ_DOMAIN": swift_params["proj_domain"],
                "PROJ_NAME": swift_params["proj_name"],
                "CONTAINER_NAME": swift_params["container_name"],
                "IN_FILES": swift_params["swift_data_str"],
                "PARTY_COUNT": len(self.all_pids),
                "SERVER_SERVICE": self.jiff_server_ip
            }

        data_template = open("{}/conclave_config.tmpl".format(self.template_directory), 'r').read()

        rendered = pystache.render(data_template, params)

        self.app.logger.info("CC YAML file:\n{}".format(rendered))

        return b64encode(rendered.encode()).decode()

    def gen_net_config(self):
        """
        Generate CC Net Config string that gets inserted into CC Config YAML.
        """

        net_str = ""

        for i in self.all_pids:
            if i == self.pid:
                net_str += "      - host: 0.0.0.0\n        port: 5000\n"
            else:
                net_str += "      - host: conclave-{0}-{1}-service\n        port: 5000\n" \
                    .format(self.timestamp, str(i))

        return net_str

    def define_config_map(self):
        """
        Populate ConfigMap template.
        """

        name = "conclave-{0}-{1}-map".format(self.timestamp, str(self.pid))
        namespace = "cici"

        data_params = \
            {
                "NAME": name,
                "NAMESPACE": namespace,
                "PROTOCOL": str(self.protocol),
                "CONF": self.conclave_config
            }

        data_template = open("{}configmap.tmpl".format(self.template_directory), 'r').read()

        rendered = pystache.render(data_template, data_params)

        return ast.literal_eval(rendered)

    def define_pod(self):
        """
        Populate Pod template.
        """

        params = \
            {
                "POD_NAME": self.name,
                "CONFIGMAP_NAME": self.config_map_name
            }

        data_template = open("{}/pod.tmpl".format(self.template_directory), 'r').read()

        rendered = pystache.render(data_template, params)

        return ast.literal_eval(rendered)

    def define_service(self):
        """
        Populate Service template for CC Pods and MPC backends.
        """

        svc = "{}-service".format(self.name)
        port = 5000

        params = \
            {
                "SERVICE_NAME": svc,
                "APP_NAME": self.name,
                "PORT": port
            }

        data_template = open("{}/service.tmpl".format(self.template_directory), 'r').read()

        rendered = pystache.render(data_template, params)

        return ast.literal_eval(rendered)

    def launch(self):
        """
        Launch all Kubernetes objects.
        """

        k_config.load_incluster_config()
        kube_client = k_client.CoreV1Api()

        try:
            api_response = kube_client.create_namespaced_config_map('cici', self.config_map_body, pretty='true')
            self.app.logger.info(
                "ConfigMap created successfully with response: \n{}\n"
                    .format(api_response))
        except ApiException as e:
            self.app.logger.error(
                "Error creating ConfigMap: \n{}\n"
                    .format(e))

        try:
            api_response = kube_client.create_namespaced_service('cici', body=self.service_body, pretty='true')
            self.app.logger.info(
                "Service created successfully with response: \n{}\n"
                    .format(api_response))
        except ApiException as e:
            self.app.logger.error(
                "Error creating Service: \n{}\n"
                    .format(e))

        try:
            api_response = kube_client.create_namespaced_pod('cici', body=self.pod_body, pretty='true')
            self.app.logger.info(
                "Pod created successfully with response: \n{}\n"
                    .format(api_response))
        except ApiException as e:
            self.app.logger.error(
                "Error creating Pod: \n{}\n"
                    .format(e))

        return
