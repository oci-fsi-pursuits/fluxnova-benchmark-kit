"""PAY-05 deterministic quote policy for fixed-target synthetic payments.

The trusted intent fixes the recipient, amount, currencies, authorized routes and
maximum source debit. Quotes are untrusted data. This module never sends payments.
"""
import re

ROUTES = ('a', 'b', 'c')
MAX_MINOR = 10**12
INTENT_FIELDS = {'payment_id', 'beneficiary', 'target_amount_minor', 'source_currency',
                 'target_currency', 'max_source_total_minor', 'authorized', 'allowed_routes'}
QUOTE_FIELDS = {'route_id', 'payment_id', 'beneficiary', 'target_amount_minor',
                'source_currency', 'target_currency', 'source_amount_minor',
                'fee_minor', 'issued_ms', 'expires_ms'}


def minor(value, *, zero=False):
    return type(value) is int and (0 if zero else 1) <= value <= MAX_MINOR


def validate_intent(intent):
    if not isinstance(intent, dict) or set(intent) != INTENT_FIELDS:
        raise ValueError('invalid_intent_fields')
    if any(not isinstance(intent[k], str) or not 1 <= len(intent[k]) <= 128
           for k in ('payment_id', 'beneficiary')):
        raise ValueError('invalid_payment_identity')
    if type(intent['authorized']) is not bool:
        raise ValueError('invalid_authority')
    if not minor(intent['target_amount_minor']) or not minor(intent['max_source_total_minor']):
        raise ValueError('invalid_payment_amount')
    if any(not isinstance(intent[k], str) or not re.fullmatch('[A-Z]{3}', intent[k])
           for k in ('source_currency', 'target_currency')):
        raise ValueError('invalid_currency')
    if intent['source_currency'] == intent['target_currency']:
        raise ValueError('fx_requires_distinct_currencies')
    routes = intent['allowed_routes']
    if (not isinstance(routes, list) or not routes or any(type(v) is not str or v not in ROUTES for v in routes)
            or len(set(routes)) != len(routes)):
        raise ValueError('invalid_route_authority')


def quote_rejection(intent, route, quote, decision_ms):
    """Return one explicit disqualification reason, or None for an eligible quote."""
    if not isinstance(quote, dict) or set(quote) != QUOTE_FIELDS:
        return 'malformed_quote'
    if quote['route_id'] != route:
        return 'route_binding_mismatch'
    if route not in intent['allowed_routes']:
        return 'route_not_authorized'
    for key in ('payment_id', 'beneficiary', 'source_currency', 'target_currency', 'target_amount_minor'):
        if type(quote[key]) is not type(intent[key]) or quote[key] != intent[key]:
            return 'payment_binding_mismatch'
    if not minor(quote['source_amount_minor']) or not minor(quote['fee_minor'], zero=True):
        return 'invalid_quote_amount'
    if any(type(quote[k]) is not int or not -60000 <= quote[k] <= 60000
           for k in ('issued_ms', 'expires_ms')) or quote['issued_ms'] >= quote['expires_ms']:
        return 'invalid_quote_validity'
    if decision_ms < quote['issued_ms']:
        return 'quote_not_yet_valid'
    if decision_ms >= quote['expires_ms']:
        return 'quote_expired'
    if quote['source_amount_minor'] + quote['fee_minor'] > intent['max_source_total_minor']:
        return 'source_debit_limit'
    return None


def select_quote(intent, quotes, decision_ms, *, minimum_valid_quotes=1):
    """Choose minimum total source debit; route ID breaks equal-cost ties.

Validity is half-open: issued_ms <= decision_ms < expires_ms. All offsets
refer to this case's trusted registration clock. None means a timed-out route.
"""
    validate_intent(intent)
    if type(decision_ms) is not int or decision_ms < 0:
        raise ValueError('invalid_decision_time')
    if type(minimum_valid_quotes) is not int or not 1 <= minimum_valid_quotes <= 3:
        raise ValueError('invalid_quote_quorum')
    if not isinstance(quotes, dict) or set(quotes) != set(ROUTES):
        raise ValueError('incomplete_quote_collection')
    eligible, rejected = [], {}
    for route in ROUTES:
        quote = quotes[route]
        reason = 'quote_timeout' if quote is None else quote_rejection(intent, route, quote, decision_ms)
        if reason:
            rejected[route] = reason
        else:
            eligible.append((quote['source_amount_minor'] + quote['fee_minor'], route))
    result = {'outcome': 'rejected' if not intent['authorized'] else 'blocked',
              'selected_route': None, 'source_total_minor': None,
              'decision_ms': decision_ms, 'eligible_routes': sorted(route for _, route in eligible),
              'disqualified_routes': rejected}
    if intent['authorized'] and len(eligible) >= minimum_valid_quotes:
        total, route = min(eligible)
        result.update(outcome='settled', selected_route=route, source_total_minor=total)
    return result
