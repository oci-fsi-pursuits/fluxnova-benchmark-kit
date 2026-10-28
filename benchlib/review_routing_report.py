"""Independent W03 original-outcome and actual review-task assertions."""
from services.review_routing import DEFINITIONS, fingerprint, snapshot


def audit_record(record):
    case=record['case'];arm=record['arm'];pid=record['process_id'];key=record['key']
    history=record['history'];payment=case['payment'];state=record['backend']
    expected=case['expected_outcome']
    required=arm=='all_review' or case['expected_policy_review']
    instance=history['instance']
    assert instance['id']==pid and instance['businessKey']==key and instance['state']=='COMPLETED'
    assert instance['processDefinitionKey']==DEFINITIONS[arm]
    for name,value in snapshot(payment,key).items():
        assert history['variables'][name]==value and type(history['variables'][name]) is type(value)
    assert history['variables']['w03Outcome']==expected
    assert set(state['receipts'])=={'route','settle'}
    for op,receipt in state['receipts'].items():
        assert receipt['case_key']==key and receipt['process_id']==pid and receipt['operation']==op
        assert receipt['input_sha256']==fingerprint(payment) and receipt['requires_review']==required
    assert state['receipts']['settle']['outcome']==expected
    wanted=[{name:payment[name] for name in ('payment_id','amount_minor','beneficiary')}] if expected=='settled' else []
    assert state['effects']==wanted
    reviews=[task for task in record['tasks'] if task['taskDefinitionKey']=='payment_review']
    assert len(record['tasks'])==len(reviews)==int(required)
    if required:
        assert reviews[0]['endTime'] and history['variables']['reviewerDecision']==case['scripted_reviewer_decision']
        assert history['variables']['reviewedPaymentSha256']==fingerprint(payment)
        complete=[op for op in record['operations'] if op.get('taskId')==reviews[0]['id'] and op.get('operationType')=='Complete']
        assert complete and all(op['userId']==record['reviewer'] for op in complete)
    else:
        assert state['receipts']['settle']['review'] is None
    ends=[a['activityId'] for a in record['activities'] if a['activityType'].lower().endswith('endevent') and a.get('endTime')]
    assert ends==[expected]
    return {'case_id':case['case_id'],'arm':arm,'outcome':expected,'effects':len(wanted),
            'review_tasks':len(reviews),'required_review_preserved':not case['expected_policy_review'] or len(reviews)==1,
            'task_duration_ms':reviews[0].get('duration') if reviews else None,
            'observed_case_duration_s':record['observed_case_duration_s'],
            'human_active_handling_time_s':None,'status':'passed'}
