"""Small, deliberately bounded BPMN upload profile for the interactive lab.

Uploaded models are code. This profile permits control flow and manual work,
not arbitrary engine-side code, connectors, forms or expression evaluation.
"""
from __future__ import annotations

import hashlib
import math
import re
import xml.etree.ElementTree as ET

BPMN = 'http://www.omg.org/spec/BPMN/20100524/MODEL'
CAMUNDA = 'http://camunda.org/schema/1.0/bpmn'
DRAWING = {'http://www.omg.org/spec/BPMN/20100524/DI',
           'http://www.omg.org/spec/DD/20100524/DC', 'http://www.omg.org/spec/DD/20100524/DI'}
ELEMENTS = {'definitions', 'process', 'startEvent', 'endEvent', 'userTask', 'manualTask',
            'serviceTask', 'exclusiveGateway', 'parallelGateway', 'sequenceFlow',
            'incoming', 'outgoing', 'conditionExpression', 'documentation'}
ATTRIBUTES = {'id', 'name', 'targetNamespace', 'isExecutable', 'sourceRef', 'targetRef',
              'default', 'gatewayDirection', 'exporter', 'exporterVersion'}
CONDITION = re.compile(r'\$\{\s*[A-Za-z_][A-Za-z0-9_]{0,63}\s*(?:==|!=|>=|<=|>|<)\s*'
                       r'(?:true|false|-?\d+(?:\.\d+)?|"[A-Za-z0-9 _-]{0,64}"|\x27[A-Za-z0-9 _-]{0,64}\x27)\s*\}')
ID = re.compile(r'[A-Za-z_][A-Za-z0-9_.-]{0,100}')


def split(name):
    if name.startswith('{'):
        return name[1:].split('}', 1)
    return '', name


def parse_model(xml, prefix):
    if not isinstance(xml, str) or len(xml.encode('utf-8')) > 256_000:
        raise ValueError('Upload a UTF-8 BPMN file smaller than 256 KB.')
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', xml, re.I):
        raise ValueError('XML entities and document types are not allowed.')
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise ValueError('The file is not well-formed BPMN XML.') from None
    if root.tag != f'{{{BPMN}}}definitions':
        raise ValueError('Expected a BPMN definitions document.')
    all_elements = list(root.iter())
    if len(all_elements) > 1500:
        raise ValueError('Model is too large for the small lab.')
    for element in all_elements:
        namespace, tag = split(element.tag)
        if namespace in DRAWING:
            continue
        if namespace != BPMN or tag not in ELEMENTS:
            raise ValueError('Unsupported BPMN element. Use basic events, gateways, user tasks or external tasks.')
        for attribute, value in element.attrib.items():
            ns, name = split(attribute)
            if '${' in value or '#{' in value:
                raise ValueError('Expressions in attributes are disabled. Use a simple sequence-flow condition instead.')
            if (not ns and name in ATTRIBUTES):
                continue
            if ns == CAMUNDA and name in {'historyTimeToLive', 'type', 'topic'}:
                if name == 'historyTimeToLive' and (tag != 'process' or not value.isdigit()):
                    raise ValueError('History retention must be a literal process setting.')
                if name == 'type' and value != 'external':
                    raise ValueError('Only external service tasks are supported.')
                if name in {'type', 'topic'} and tag != 'serviceTask':
                    raise ValueError('External-task attributes must belong to a service task.')
                continue
            if ns == 'http://www.w3.org/2001/XMLSchema-instance' and name == 'type' and tag == 'conditionExpression' and value.endswith('tFormalExpression'):
                continue
            raise ValueError('Unsupported attribute. Scripts, delegates, listeners, forms and connectors are disabled in the lab.')
        if tag == 'conditionExpression' and not CONDITION.fullmatch((element.text or '').strip()):
            raise ValueError('Conditions must compare one scalar variable with a literal, e.g. ${approved == true}.')
        if tag == 'serviceTask' and (element.get(f'{{{CAMUNDA}}}type') != 'external' or not element.get(f'{{{CAMUNDA}}}topic')):
            raise ValueError('Service tasks must be external tasks with a topic; complete them manually in this lab.')
    processes = root.findall(f'{{{BPMN}}}process')
    if len(processes) != 1:
        raise ValueError('Upload exactly one process per file.')
    process = processes[0]
    flow_nodes = ELEMENTS - {'definitions', 'process', 'incoming', 'outgoing', 'conditionExpression', 'documentation'}
    for e in process.iter():
        tag = split(e.tag)[1]
        allowed = flow_nodes | {'documentation'} if e is process else {'documentation'}
        if tag in flow_nodes:
            allowed |= {'incoming', 'outgoing'}
        if tag == 'sequenceFlow':
            allowed |= {'conditionExpression'}
        if any(split(child.tag)[0] != BPMN or split(child.tag)[1] not in allowed for child in e):
            raise ValueError('Nested processes or unsupported child elements are outside the lab profile.')
    # Discard drawing metadata after validation; the UI renders the actual graph.
    for child in list(root):
        if child is not process:
            root.remove(child)
    ids = [e.get('id') for e in process.iter() if e.get('id')]
    if len(ids) != len(set(ids)) or any(not ID.fullmatch(i) for i in ids):
        raise ValueError('Use unique, simple XML identifiers.')
    nodes, edges = [], []
    for e in process:
        tag = split(e.tag)[1]
        if tag in {'documentation'}:
            continue
        if not e.get('id'):
            raise ValueError('Every flow node and sequence flow needs an id.')
        if tag == 'sequenceFlow':
            edges.append({'id': e.get('id'), 'source': e.get('sourceRef'), 'target': e.get('targetRef')})
        else:
            nodes.append({'id': e.get('id'), 'name': (e.get('name') or e.get('id'))[:100],
                          'type': tag, 'topic': e.get(f'{{{CAMUNDA}}}topic')})
        if tag == 'serviceTask':
            e.set(f'{{{CAMUNDA}}}topic', prefix + '_tasks')
    node_ids = {n['id'] for n in nodes}
    if not 2 <= len(nodes) <= 50 or any(e['source'] not in node_ids or e['target'] not in node_ids for e in edges):
        raise ValueError('Use 2–50 nodes and valid flow references.')
    if sum(n['type'] == 'startEvent' for n in nodes) != 1 or not any(n['type'] == 'endEvent' for n in nodes):
        raise ValueError('The lab needs one plain start event and at least one plain end event.')
    process.set('id', prefix)
    process.set('isExecutable', 'true')
    process.set(f'{{{CAMUNDA}}}historyTimeToLive', '1')
    return {'name': (process.get('name') or 'Uploaded workflow')[:100], 'key': prefix,
            'nodes': nodes, 'edges': edges, 'source_sha256': hashlib.sha256(xml.encode()).hexdigest(),
            'xml': ET.tostring(root, encoding='utf-8', xml_declaration=True)}


def variables(values):
    if not isinstance(values, dict) or len(values) > 32:
        raise ValueError('Variables must be a JSON object with at most 32 scalar values.')
    result = {}
    for key, value in values.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', key):
            raise ValueError('Variable names must be simple identifiers.')
        kind = {bool: 'Boolean', str: 'String', int: 'Long', float: 'Double', type(None): 'Null'}.get(type(value))
        if not kind or (isinstance(value, str) and len(value) > 2000) or (type(value) == int and abs(value) > 2**53 - 1) or (type(value) == float and not math.isfinite(value)):
            raise ValueError('Use finite JSON scalar values, short strings and safe integers.')
        result[key] = {'value': value, 'type': kind}
    return result


def parameters(data, model):
    result = {}
    for key, default, low, high in [('count', 1, 1, 20), ('delay_ms', 500, 0, 2000),
                                    ('poll_ms', 250, 100, 1000), ('deadline_s', 60, 1, 600)]:
        value = data.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{key} must be an integer between {low} and {high}.')
        result[key] = value
    result['variables'] = variables(data.get('variables', {}))
    result['expected_end'] = data.get('expected_end', '')
    if result['expected_end'] and result['expected_end'] not in {n['id'] for n in model['nodes'] if n['type'] == 'endEvent'}:
        raise ValueError('Choose an expected end event from this model.')
    return result
