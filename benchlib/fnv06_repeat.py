"""Fixed qualification cells preceding the separately declared 45-cell study."""
import copy


def cell_protocol(protocol, cell_id):
    expected = [{'id': 'C' + str(i + 1).zfill(2), 'kind': kind, 'population': n}
                for i, (kind, n) in enumerate((kind, n) for kind in ('message', 'signal', 'batch') for n in (10, 1000))]
    if (protocol.get('study_id') != 'FNV-06-REPEAT-QUAL' or protocol.get('cells') != expected
            or protocol.get('population_sizes') != [10, 100, 1000]
            or protocol.get('warmup_recipients') != 100
            or cell_id not in {row['id'] for row in expected}):
        raise ValueError('Only the fixed six-cell warmup qualification is supported')
    required = {'message_client_ceiling': 4, 'controls_per_observation': 2, 'release_lead_ms': 3000,
                'maximum_first_dispatch_lag_ms': 1000, 'completion_poll_ms': 100,
                'completion_timeout_s': 120, 'batch_terminal_timeout_s': 240}
    if any(type(protocol.get(k)) is not int or protocol[k] != v for k, v in required.items()):
        raise ValueError('Qualification controls changed')
    result = copy.deepcopy(protocol)
    cell = next(r for r in result['cells'] if r['id'] == cell_id)
    result['qualification_cell'] = cell
    result['observations'] = [{'id': cell_id + phase, 'kind': cell['kind'], 'population': n}
                              for phase, n in [('W', 100), ('M', cell['population'])]]
    return result
