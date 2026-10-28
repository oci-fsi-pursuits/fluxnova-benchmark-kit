"""Record an explicit benchmark review through the current engine identity."""
import argparse
import json
import os
from pathlib import Path

from services.payment_approval import Approval, clients
from services.recoverable_agent import owner


def review(directory, decision, bound_intent=None):
    with owner(directory):
        if bound_intent is not None:
            from services.bound_payment import BoundReview
            return BoundReview(directory, *clients(), bound_intent).decide(decision, os.environ['BENCH_USERNAME'])
        return Approval(directory, *clients()).decide(decision, reviewer_identity=os.environ['BENCH_USERNAME'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-directory', type=Path, required=True)
    parser.add_argument('--decision', choices=('approve', 'reject'), required=True)
    parser.add_argument('--bound-intent', type=Path)
    args = parser.parse_args()
    intent = json.loads(args.bound_intent.read_text()) if args.bound_intent else None
    print(json.dumps(review(args.review_directory, args.decision, intent)))
