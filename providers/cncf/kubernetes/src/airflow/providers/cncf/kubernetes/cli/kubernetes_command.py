# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Kubernetes sub-commands."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

from kubernetes import client
from kubernetes.client.api_client import ApiClient
from kubernetes.client.rest import ApiException

from airflow.models import DagModel, DagRun, TaskInstance
from airflow.providers.cncf.kubernetes import pod_generator
from airflow.providers.cncf.kubernetes.executors.kubernetes_executor import KubeConfig
from airflow.providers.cncf.kubernetes.kube_client import get_kube_client
from airflow.providers.cncf.kubernetes.kubernetes_helper_functions import create_unique_id
from airflow.providers.cncf.kubernetes.pod_generator import PodGenerator, generate_pod_command_args
from airflow.providers.cncf.kubernetes.version_compat import AIRFLOW_V_3_0_PLUS
from airflow.utils import cli as cli_utils, yaml
from airflow.utils.cli import get_dag
from airflow.utils.providers_configuration_loader import providers_configuration_loaded
from airflow.utils.types import DagRunType


@cli_utils.action_cli
@providers_configuration_loaded
def generate_pod_yaml(args):
    """Generate yaml files for each task in the DAG. Used for testing output of KubernetesExecutor."""
    logical_date = args.logical_date if AIRFLOW_V_3_0_PLUS else args.execution_date
    if AIRFLOW_V_3_0_PLUS:
        dag = get_dag(bundle_names=args.bundle_name, dag_id=args.dag_id)
    else:
        dag = get_dag(subdir=args.subdir, dag_id=args.dag_id)
    yaml_output_path = args.output_path

    dm = DagModel(dag_id=dag.dag_id)

    if AIRFLOW_V_3_0_PLUS:
        dr = DagRun(dag.dag_id, logical_date=logical_date)
        dr.run_id = DagRun.generate_run_id(
            run_type=DagRunType.MANUAL, logical_date=logical_date, run_after=logical_date
        )
        dm.bundle_name = args.bundle_name if args.bundle_name else "default"
        dm.relative_fileloc = dag.relative_fileloc
    else:
        dr = DagRun(dag.dag_id, execution_date=logical_date)
        dr.run_id = DagRun.generate_run_id(run_type=DagRunType.MANUAL, execution_date=logical_date)

    kube_config = KubeConfig()

    for task in dag.tasks:
        if AIRFLOW_V_3_0_PLUS:
            from uuid6 import uuid7

            ti = TaskInstance(task, run_id=dr.run_id, dag_version_id=uuid7())
        else:
            ti = TaskInstance(task, run_id=dr.run_id)
        ti.dag_run = dr
        ti.dag_model = dm

        command_args = generate_pod_command_args(ti)
        pod = PodGenerator.construct_pod(
            dag_id=args.dag_id,
            task_id=ti.task_id,
            pod_id=create_unique_id(args.dag_id, ti.task_id),
            try_number=ti.try_number,
            kube_image=kube_config.kube_image,
            date=ti.logical_date if AIRFLOW_V_3_0_PLUS else ti.execution_date,
            args=command_args,
            pod_override_object=PodGenerator.from_obj(ti.executor_config),
            scheduler_job_id="worker-config",
            namespace=kube_config.executor_namespace,
            base_worker_pod=PodGenerator.deserialize_model_file(kube_config.pod_template_file),
            with_mutation_hook=True,
        )
        api_client = ApiClient()
        date_string = pod_generator.datetime_to_label_safe_datestring(logical_date)
        yaml_file_name = f"{args.dag_id}_{ti.task_id}_{date_string}.yml"
        os.makedirs(os.path.dirname(yaml_output_path + "/airflow_yaml_output/"), exist_ok=True)
        with open(yaml_output_path + "/airflow_yaml_output/" + yaml_file_name, "w") as output:
            sanitized_pod = api_client.sanitize_for_serialization(pod)
            output.write(yaml.dump(sanitized_pod))
    print(f"YAML output can be found at {yaml_output_path}/airflow_yaml_output/")


@cli_utils.action_cli
@providers_configuration_loaded
def cleanup_pods(args):
    """Clean up k8s pods in evicted/failed/succeeded/pending states."""
    namespace = args.namespace

    min_pending_minutes = args.min_pending_minutes
    # protect newly created pods from deletion
    if min_pending_minutes < 5:
        min_pending_minutes = 5

    # https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/
    # All Containers in the Pod have terminated in success, and will not be restarted.
    pod_succeeded = "succeeded"

    # The Pod has been accepted by the Kubernetes cluster,
    # but one or more of the containers has not been set up and made ready to run.
    pod_pending = "pending"

    # All Containers in the Pod have terminated, and at least one Container has terminated in failure.
    # That is, the Container either exited with non-zero status or was terminated by the system.
    pod_failed = "failed"

    # https://kubernetes.io/docs/tasks/administer-cluster/out-of-resource/
    pod_reason_evicted = "evicted"
    # If pod is failed and restartPolicy is:
    # * Always: Restart Container; Pod phase stays Running.
    # * OnFailure: Restart Container; Pod phase stays Running.
    # * Never: Pod phase becomes Failed.
    pod_restart_policy_never = "never"

    print("Loading Kubernetes configuration")
    kube_client = get_kube_client()
    print(f"Listing pods in namespace {namespace}")
    airflow_pod_labels = [
        "dag_id",
        "task_id",
        "try_number",
        "airflow_version",
    ]
    list_kwargs = {"namespace": namespace, "limit": 500, "label_selector": ",".join(airflow_pod_labels)}

    while True:
        pod_list = kube_client.list_namespaced_pod(**list_kwargs)
        for pod in pod_list.items:
            pod_name = pod.metadata.name
            print(f"Inspecting pod {pod_name}")
            pod_phase = pod.status.phase.lower()
            pod_reason = pod.status.reason.lower() if pod.status.reason else ""
            pod_restart_policy = pod.spec.restart_policy.lower()
            current_time = datetime.now(pod.metadata.creation_timestamp.tzinfo)

            if (
                pod_phase == pod_succeeded
                or (pod_phase == pod_failed and pod_restart_policy == pod_restart_policy_never)
                or (pod_reason == pod_reason_evicted)
                or (
                    pod_phase == pod_pending
                    and current_time - pod.metadata.creation_timestamp
                    > timedelta(minutes=min_pending_minutes)
                )
            ):
                print(
                    f'Deleting pod "{pod_name}" phase "{pod_phase}" and reason "{pod_reason}", '
                    f'restart policy "{pod_restart_policy}"'
                )
                try:
                    _delete_pod(pod.metadata.name, namespace)
                except ApiException as e:
                    print(f"Can't remove POD: {e}", file=sys.stderr)
            else:
                print(f"No action taken on pod {pod_name}")
        continue_token = pod_list.metadata._continue
        if not continue_token:
            break
        list_kwargs["_continue"] = continue_token


def _delete_pod(name, namespace):
    """
    Delete a namespaced pod.

    Helper Function for cleanup_pods.
    """
    kube_client = get_kube_client()
    delete_options = client.V1DeleteOptions()
    print(f'Deleting POD "{name}" from "{namespace}" namespace')
    api_response = kube_client.delete_namespaced_pod(name=name, namespace=namespace, body=delete_options)
    print(api_response)
