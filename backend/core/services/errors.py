"""
Business-rule errors raised by the service layer.

Subclassing DRF's APIException means a service can simply `raise
BusinessRuleError("...")` and the API returns a clean HTTP 400 with
`{"detail": "..."}` — no try/except boilerplate in every view. Because the
services run inside `transaction.atomic()`, raising also rolls back every
database change made so far in that operation.
"""
from rest_framework.exceptions import APIException


class BusinessRuleError(APIException):
    status_code = 400
    default_detail = 'This action is not allowed.'
    default_code = 'business_rule_violation'
