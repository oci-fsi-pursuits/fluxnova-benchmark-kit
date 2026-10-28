"""Prepare the 22 October slide content without editing a PowerPoint binary.

Retains the reviewed 21 October story and binds new claims to committed evidence.
The source is ready for a separate presentation export and visual review.
"""
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'e3facb2'
BASE = f'https://github.com/oci-fsi-pursuits/fluxnova-benchmarking/blob/{COMMIT}/'
INPUTS = [
    'presentations/fluxnova-story-20261021.json',
    'validation/oci-20261022-pool-binding/summary.json',
    'validation/oci-20261022-corrected-link-loss/summary.json',
    'validation/oci-20261022-key-lifecycle/summary.json',
    'validation/oci-20261022-key-lifecycle-rollout/summary.json',
    'validation/oci-20261022-identity-recovery/summary.json',
    'validation/oci-20261022-closeout/summary.json',
]


def load(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'))


def block(text, y, h, size=23, bold=False, color='263A38'):
    return dict(text=text, x=48, y=y, w=864, h=h, size=size,
                bold=bold, color=color)


def table(headers, rows, widths, heights, y=194, size=19):
    return dict(headers=headers, rows=rows, widths=widths, rowHeights=heights,
                x=48, y=y, fontSize=size)


def slide(title, blocks, evidence, notes, tables=None):
    return dict(title=title, blocks=blocks, tables=tables or [], lines=[], rects=[],
                source='Scoped evidence through 22 October 2026 UTC. Sources in speaker notes.',
                notes=notes + '\n\nSources\n' + '\n'.join(BASE + p for p in evidence))


def main():
    original, pool, plain, lifecycle, rollout, identity, cost = map(load, INPUTS)
    assert len(original['slides']) == 18
    assert pool['status'] == plain['status'] == 'pass'
    assert pool['control']['post_critical_jdbc_counts'] == [9]
    assert pool['candidate']['post_critical_jdbc_counts'] == [10]
    assert plain['post_critical_samples'] == 340
    assert plain['counts']['settlement_effects'] == 17
    assert not pool['original_cell_reconfigured'] and not plain['original_cell_reconfigured']
    assert lifecycle['status'] == 'pass_isolated_oci_rehearsal'
    assert rollout['status'] == 'pass_deployed' and rollout['registry']['after_intents'] == 33
    assert rollout['deployment']['revoked_key_count'] == 0
    assert identity['clean_confirmation']['status'] == 'pass'
    assert identity['declared_negative_controls']['total'] == 21
    assert identity['payment_effects'] == 3 and identity['integrity_rejections_without_effects'] == 1
    assert cost['matched_computed_amounts']['USD'] == '54.080029072909'
    assert not cost['complete_campaign_cost'] and all(cost['checks'].values())

    slides = copy.deepcopy(original['slides'][:15])
    for item in slides[0]['blocks']:
        item['text'] = item['text'].replace('21 October 2026 UTC', '22 October 2026 UTC')
    slides[0]['notes'] += '\n22 October adds bounded recovery/control evidence and a partial posted-cost reconciliation. It changes no historical performance result.'

    slides.append(slide('A configuration correction restored the test pool',
        [block('One environment-variable spelling caused a startup checkout in the recovery harness.', 130, 58, 25, True),
         block('The original reference engine stayed unchanged. This result does not measure a throughput gain or provider failover.', 439, 60, 21, True, '9A5B23')],
        ['validation/oci-20261022-pool-binding/README.md',
         'validation/oci-20261022-corrected-link-loss/README.md'],
        'The old CONNECTION_TIMEOUT spelling still bound the intended 5000 ms value but also invoked a connection getter during Spring property binding in this exact recovery fixture. Canonical CONNECTIONTIMEOUT avoided that extra startup checkout. Matched diagnostics kept the live effective 5000/2000 ms timeouts and pool maximum/minimum ten. Control returned nine sessions in 336/336 post-critical samples, candidate ten in 331/331. The later confirmation removed diagnostic instrumentation and retained ten new TLS sessions in 340/340 samples. All 17 accepted workflows settled once; two failed starts had no workflow history or effect. Independent restore and original-state checks passed. The mechanism belongs to the fault harness and does not explain every earlier application stall. None of these samples establish application RTO, maximum capacity or unplanned provider HA.',
        [table(['Configuration or confirmation', 'Observed recovery'], [
            ['Original variable spelling', '9 sessions in 336/336 samples'],
            ['Canonical spelling, same numeric value', '10 sessions in 331/331 samples'],
            ['Canonical spelling without diagnostics', '10 sessions in 340/340 samples']],
            [450, 414], [38, 64, 64, 64], y=204)]))

    slides.append(slide('Signing authority survived the code and key changes',
        [block('Original issuance identities and receipts remained intact through activation and restart.', 130, 60, 25, True),
         block('The deployed verifier retains Vault version 2 and the real key policy. No real key was revoked or retired.', 438, 62, 21, True, '075B51')],
        ['validation/oci-20261022-key-rotation-rollout/README.md',
         'validation/oci-20261022-key-lifecycle/README.md',
         'validation/oci-20261022-key-lifecycle-rollout/README.md'],
        'Scheduled rotation moved the persistent verifier to numeric Vault version 2 while retaining legacy verification material. Later lifecycle code passed a separate isolated OCI rehearsal for revoked unsent work, existing receipt recovery, a fresh issuer after active-key revocation and unused-key retirement. Referenced-key retirement stayed refused. The subsequent code-only rollout preserved all 30 prior issuance identities before activation, afterward and after another restart. Its two valid synthetic payments settled once and its tampered token produced no effect. All 33 final registry MACs verified; policy generation, configuration and the unit stayed unchanged. This is continuity of existing authority and tested lifecycle behavior, not an actual compromise response or secure erasure. HMAC authority, registry, engine and operator trust remain part of the boundary.',
        [table(['Boundary', 'Verified result'], [
            ['Scheduled key rotation', 'Old and new approvals each settle once'],
            ['Isolated revocation and retirement', 'Unsent revoked work blocked, receipts retained'],
            ['Persistent lifecycle-code deployment', 'All 30 earlier request identities replay unchanged']],
            [390, 474], [38, 64, 64, 64], y=204)]))

    slides.append(slide('Recovered permissions preserved the approval boundary',
        [block('Four database roles and two memberships recovered with engine users, groups and grants.', 130, 60, 25, True),
         block('Three exact effects and one zero-effect rejection. External SSO and OCI IAM recovery remain open.', 439, 60, 21, True, '075B51')],
        ['validation/oci-20261022-identity-recovery/README.md'],
        'The clean fourth cohort restored synthetic PostgreSQL cluster roles, memberships, owners, schema/object/default grants and internal engine identities. The original pending task, issuance tokens and committed receipt survived. Eleven permission checks returned HTTP 403, one intentional old reviewer password returned HTTP 401, four SQL checks returned SQLSTATE 42501, four database logins failed and a missing-role transactional restore failed without leaving a partial schema. Additional read-only authentication observations waited out the deliberate failed-login lock and are not extra declared controls. Removing reviewer group membership blocked approval without changing the task or ledger; restoring the intended membership permitted only the registered reviewer. Four payment cases produced eight completed screening/review histories and three effects. Two independent restores matched 49 tables/422 rows and detected deliberately altered data and membership. Failed preparations and controlled continuations remained archived; the final cohort passed cleanly. No corporate IdP, SSO/MFA, OCI IAM, provider-managed role, managed TLS or global policy authority was restored.',
        [table(['Control after restore', 'Outcome'], [
            ['Agent or outsider attempts approval', 'Denied'],
            ['Reviewer loses group membership', 'Approval denied, task and ledger unchanged'],
            ['Intended reviewer membership restored', 'Original pending payment settles once']],
            [405, 459], [38, 64, 64, 64], y=204)]))

    slides.append(slide('The current campaign is at reporting closeout', [],
        ['docs/campaign-completion.md', 'validation/oci-20261022-closeout/README.md'],
        'The five-repeat async reference, separate five-repeat settlement reference and ten-run matched worker-timing comparison are complete. Keep the worker pacing change, 100 ms predeadline polling and scoped diagnostic visibility. Two deferred recovery integrations need external prerequisites: a supported abrupt primary-fault method, and an actual external identity/OCI IAM recovery configuration. Neither calls for another baseline repetition. The two reporting deliverables are a reconciled cost disclosure and this updated story. The 22 October snapshot provides 54.08 USD in receipt-backed posted charges, while full campaign attribution and dollars per outcome remain unestablished. The original cell remains allocated. Production sizing, longer acceptance/soak, real payment rails, wider model quality, human usability, boot-volume/region loss and distributed/global-authority recovery need separate scope. Earlier intermittent startup and deadline failures remain unresolved; later passes do not erase them.',
        [table(['Workstream', 'Decision or remaining boundary'], [
            ['Fixed-rate measurement', 'References and matched study complete'],
            ['Worker settings', 'Keep immediate fetch and 100 ms predeadline polling'],
            ['Recovery integrations', 'External identity/IAM and provider abrupt-fault method'],
            ['Cost reporting', '$54.08 matched posted charges, full attribution open'],
            ['Further production work', 'Separate scope and acceptance criteria']],
            [300, 564], [38, 58, 58, 58, 58, 58], y=145)]))

    slides.append(copy.deepcopy(original['slides'][16]))
    allocation = copy.deepcopy(original['slides'][17])
    allocation['blocks'][2]['text'] = 'Current cost snapshot and planning coverage'
    allocation['blocks'][3]['text'] = 'Matched posted charges: $54.08 (partial)\nPlanning reserve: $209.18 of $300 through 23 Oct 01:00 UTC'
    allocation['blocks'][4]['text'] = 'Billing remains partial. The original cell stays allocated and billable.'
    allocation['source'] = 'Read-only inventory and posted-cost reconciliation, 22 October 2026 UTC.'
    allocation['notes'] = ('The original fixed allocation remains four Compute VMs at 8 OCPUs/48 GB plus one managed PostgreSQL node at 2 OCPUs/32 GB, for 10 OCPUs/80 GB total. The 21:19 UTC Usage API response yields 54.080029072909 USD of matched posted charges after time-window reconciliation. The latest returned matched interval ends at 12:00 UTC. Identified deleted recovery databases contribute 4.520812311765 USD and backups 0.004350932454 USD within that total. The hourly response also returned prior-day rows outside its request: 253 matched rows totalling 7.969347818571 USD match the already-counted daily values exactly across 15 resource/service/currency groups. Excluding them avoids counting the same cost twice. This total excludes unmatched/shared/model costs, unreported intervals and future running time. No complete actual cost per payment or comparative savings figure is available. The 209.18 USD conservative cumulative reservation under the 300 USD ceiling through 23 October 01:00 UTC remains a different planning quantity, not a hard cutoff or invoice. Resources remain allocated and billable. No resource was created by the cost refresh.\n\nSources\n' + BASE + 'validation/oci-20261022-closeout/README.md')
    slides.append(allocation)

    assert len(slides) == 21
    for item in slides:
        for spec in item.get('tables', []):
            assert sum(spec['widths']) == 864
            assert len(spec['rowHeights']) == len(spec['rows']) + 1
            assert all(len(row) == len(spec['headers']) for row in spec['rows'])
            assert spec['y'] + sum(spec['rowHeights']) <= 505
    result = copy.deepcopy(original)
    result.update(source_commit=COMMIT, as_of_utc='2026-10-22',
                  prior_story='presentations/fluxnova-story-20261021.json', slides=slides,
                  input_sha256={name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in INPUTS},
                  output_status='content_prepared_not_exported_or_visually_validated')
    target = ROOT/'presentations/fluxnova-story-20261022.json'
    target.write_bytes((json.dumps(result, indent=2, ensure_ascii=False)+'\n').encode('utf-8'))
    print(json.dumps({'source': str(target), 'slides': len(slides),
                      'native_tables_planned': sum(len(s.get('tables', [])) for s in slides),
                      'pptx_exported': False}))


if __name__ == '__main__':
    main()
