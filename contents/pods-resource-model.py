#!/usr/bin/env python -u
import logging
import sys
import os
import common
import json
import shlex

from kubernetes import client


class JsonQuery(dict):
    def get(self, path, default=None):
        keys = path.split(".")
        val = None

        for key in keys:
            if val:
                if isinstance(val, list):
                    val = [v.get(key, default) if v else None for v in val]
                else:
                    val = val.get(key, default)
            else:
                val = dict.get(self, key, default)

            if not val:
                break

        return val


logging.basicConfig(stream=sys.stderr,
                    level=logging.INFO,
                    format='%(levelname)s: %(name)s: %(message)s'
                    )
log = logging.getLogger('kubernetes-model-source')


def nodeCollectData(pod, defaults, taglist, mappingList):
    tags = []
    tags.extend(taglist.split(','))

    status = pod.status.phase
    statusMessage = None
    startedAt = None

    terminated = False
    image = None
    container_id = None
    container_name = None

    if pod.status.container_statuses:
        for statuses in pod.status.container_statuses:
            if statuses.state.running is not None:
                status = "running"
                if statuses.state.running.started_at:
                    startedAt = statuses.state.running.started_at.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )

            if statuses.state.waiting is not None:
                status = "waiting"

            if statuses.state.terminated is not None:
                terminated = True
                status = "terminated"

        image = pod.status.container_statuses[0].image
        container_id = pod.status.container_statuses[0].container_id
        container_name = pod.status.container_statuses[0].name

    if terminated is False:
        for info in pod.status.conditions:
            if (info.status == 'False'):
                status = info.reason
                statusMessage = info.message

    labels = []

    if pod.metadata.labels:
        for keys, values in pod.metadata.labels.items():
            labels.append(keys + ":" + values)

    default_settings = {
        'default:pod_id': pod.status.pod_ip,
        'default:host_id': pod.status.host_ip,
        'default:started_at': startedAt,
        'default:name': pod.metadata.name,
        'default:labels': ','.join(labels),
        'default:namespace': pod.metadata.namespace,
        'default:image': image,
        'default:status': status,
        'default:status_message': statusMessage,
        'default:container_id': container_id,
        'default:container_name': container_name
    }

    mappings = []
    custom_attributes = {}

    # custom mapping attributes
    if mappingList:
        log.debug('Mapping: %s' % mappingList)
        mappings.extend(mappingList.split(','))

        for mapping in mappings:
            mapping_array = dict(s.split('=', 1) for s in mapping.split())

            for key, value in mapping_array.items():
                if key.find(".selector"):
                    attribute = key.replace(".selector", "")
                    custom_attribute = None
                    # take the values from default
                    if "default:" in value:
                        custom_attribute = default_settings[value]
                    else:
                        # taking the values from docker inspect
                        for item in json:
                            custom_attribute = JsonQuery(item).get(value)

                    if custom_attribute:
                        custom_attributes[attribute] = custom_attribute

        log.debug('Custom Attributes: %s' % custom_attributes)

    # rundeck attributes
    data = default_settings
    data['nodename'] = default_settings['default:name']
    data['hostname'] = default_settings['default:pod_id']
    data['terminated'] = terminated

    emoticon = ""
    if default_settings['default:status'] == "running":
        emoticon = u'\U0001f44d'
    if default_settings['default:status'] == "terminated":
        emoticon = u'\U00002705'
    if default_settings['default:status'] == "ContainersNotReady":
        emoticon = u'\U0000274c'
    if default_settings['default:status'] == "waiting":
        emoticon = u'\U0000274c'

    data['status'] = emoticon + " " + default_settings['default:status']

    desc = emoticon + " " + default_settings['default:status']
    if default_settings['default:status_message']:
        desc = desc + "(" + default_settings['default:status_message'] + ")"

    data['description'] = desc

    final_tags = ["pods"]

    for tag in tags:
        if "tag.selector=" in tag:
            custom_tag = data[tag.replace("tag.selector=", "")]
            final_tags.append(custom_tag)
        else:
            final_tags.append(tag)

    data['tags'] = ','.join(final_tags)

    if custom_attributes:
        data = dict(data.items() + custom_attributes.items())

    data.update(dict(token.split('=') for token in shlex.split(defaults)))

    return data


def main():
    if os.environ.get('RD_CONFIG_DEBUG') == 'true':
        log.setLevel(logging.DEBUG)
        log.debug("Log level configured for DEBUG")

    common.connect()

    tags = os.environ.get('RD_CONFIG_TAGS')
    mappingList = os.environ.get('RD_CONFIG_MAPPING')
    defaults = os.environ.get('RD_CONFIG_ATTRIBUTES')

    running = False
    if os.environ.get('RD_CONFIG_RUNNING') == 'true':
        running = True

    field_selector = None
    if os.environ.get('RD_CONFIG_FIELD_SELECTOR'):
        field_selector = os.environ.get('RD_CONFIG_FIELD_SELECTOR')

    node_set = []
    v1 = client.CoreV1Api()
    ret = v1.list_pod_for_all_namespaces(
        watch=False,
        field_selector=field_selector
    )

    for i in ret.items:
        log.debug("%s\t%s\t%s" % (i.status.pod_ip,
                                  i.metadata.namespace,
                                  i.metadata.name))

        node_data = nodeCollectData(i,
                                    defaults,
                                    tags,
                                    mappingList)

        if running is False:
            if(node_data["terminated"] is False):
                node_set.append(node_data)

        if running is True:
            if node_data["status"] == "Running":
                node_set.append(node_data)

    print json.dumps(node_set, indent=4, sort_keys=True)


if __name__ == '__main__':
    main()
