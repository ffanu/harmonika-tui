#!/usr/bin/env python3
"""Smoke installed CLI/public reporting with synthetic data; never print API keys."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('report_fixture', ROOT / 'tests/erp/test_reporting.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def request(operation, data):
    process = subprocess.run(['harmonika', 'erp', 'analyze', operation], input=json.dumps(data),
                             capture_output=True, text=True, timeout=45)
    return json.loads(process.stdout)



def main():
    data = fixture.Reporting().fixture()
    results = []
    for operation in ['finance.financial.statement', 'report.financial.statement',
                      'finance.cashflow.statement', 'report.cash.flow', 'report.profit.loss']:
        response = request(operation, data)
        assert not response.get('error'), 'public_report_failed:' + operation
        result = response['result']
        if 'balance_sheet' in result:
            assert result['balance_sheet']['assets_minor'] == '1150'
            assert result['balance_sheet']['imbalance_minor'] == '0'
        if 'profit_loss' in result:
            assert result['profit_loss']['profit_minor'] == '150'
        if 'cash_flow' in result:
            assert result['cash_flow']['opening_minor'] == '1000'
            assert result['cash_flow']['closing_minor'] == '950'
        assert not result['bank_reconciled'] and not result['source_completeness_verified']
        assert not result['ledger_posted']
        results.append({'operation': operation, 'passed': True, 'report_type': result['report_type']})
    invalid = copy.deepcopy(data)
    invalid['journals'][0]['lines'][0]['account_code'] = 'unknown'
    assert request('finance.financial.statement', invalid).get('error'), 'invalid_input_accepted'
    results.append({'invalid_account_rejected': True})
    (ROOT / 'docs/ERP-PUBLIC-REPORTING-RESULT.json').write_text(
        json.dumps({'synthetic_input_only': True, 'tests': results}, indent=2) + '\n')
    print(json.dumps({'public_report_views_passed': 5, 'invalid_input_rejected': True}))


if __name__ == '__main__':
    main()
