import json
import os

_data_cache = None
_live_cache = None


def load_workbook_data():
    global _data_cache
    if _data_cache is not None:
        return _data_cache

    data_path = os.path.join(os.path.dirname(__file__), 'workbook_data.json')
    with open(data_path, 'r') as f:
        _data_cache = json.load(f)
    return _data_cache


def invalidate_live_cache():
    global _live_cache
    _live_cache = None


def get_live_data():
    global _live_cache
    if _live_cache is not None:
        return _live_cache

    from models import db, MarketInput, SharePrice, CompanyFinancials
    from compute import recalculate_all, get_active_inputs

    base = load_workbook_data()
    json_inputs = base.get('inputs', {})

    try:
        db_inputs = MarketInput.query.all()
        db_prices = SharePrice.query.all()
        db_financials = CompanyFinancials.query.all()
    except Exception:
        _live_cache = base
        return _live_cache

    if not db_inputs and not db_prices and not db_financials:
        _live_cache = base
        return _live_cache

    merged_inputs = get_active_inputs(db_inputs, json_inputs)
    _live_cache = recalculate_all(merged_inputs, base, db_prices, db_financials)
    return _live_cache

def get_dashboard_data():
    data = get_live_data()
    return {
        'at_a_glance': data.get('at_a_glance', {}),
        'nimed_index': data.get('nimed_index', {}),
        'bank_credit_risk': data.get('bank_credit_risk', []),
        'valuations': data.get('valuations', []),
        'index_timeseries': data.get('index_timeseries', []),
        '_sources': data.get('_sources', {}),
    }

def get_beta_data():
    data = get_live_data()
    return {
        'betas': data.get('betas', []),
        'overview': data.get('overview', {}),
        'inputs': data.get('inputs', {}),
    }

def get_valuation_data():
    data = get_live_data()
    return {
        'valuations': data.get('valuations', []),
        'valuation_details': data.get('valuation_details', []),
        'model_weights': data.get('model_weights', []),
        'sector_weights': data.get('sector_weights', []),
    }

def get_credit_risk_data():
    data = get_live_data()
    return {
        'bank_credit_risk': data.get('bank_credit_risk', []),
        'credit_scoring': data.get('credit_scoring', []),
        'credit_scores_detail': data.get('credit_scores_detail', []),
        'ratios': data.get('ratios', []),
    }

def get_fixed_income_data():
    data = get_live_data()
    return {
        'bond_calc': data.get('bond_calc', {}),
        'yield_curve': data.get('yield_curve', []),
    }

def get_peer_data():
    data = get_live_data()
    return {
        'all_peers': data.get('all_peers', {}),
        'peer_banks': data.get('peer_banks', []),
    }


def get_financial_statements_data():
    data = get_live_data()
    return data.get('financial_statements', {})


def get_shareholder_ratios_data():
    data = get_live_data()
    return data.get('shareholder_ratios', [])

def get_checks_data():
    data = get_live_data()
    return {
        'checks': data.get('checks', []),
        'overall_status': data.get('overall_status', ''),
        'fs_checks': data.get('fs_checks', []),
    }

def get_inputs_data():
    data = get_live_data()
    return {
        'inputs': data.get('inputs', {}),
        'credit_scoring': data.get('credit_scoring', []),
    }

def get_index_data():
    data = get_live_data()
    return {
        'index_timeseries': data.get('index_timeseries', []),
        'nimed_index': data.get('nimed_index', {}),
    }


_yin_cache = None


def _normalize_yin_data(data):
    for key, val in data.items():
        if not isinstance(val, list):
            continue
        for row in val:
            if 'c1' not in row:
                row['c1'] = ''
            elif row['c1'] is None:
                row['c1'] = ''
            if 'c2' not in row:
                row['c2'] = ''
            elif row['c2'] is None:
                row['c2'] = ''
            for i in range(3, 13):
                k = f'c{i}'
                if k not in row:
                    row[k] = None
                elif isinstance(row[k], str):
                    try:
                        row[k] = float(row[k])
                    except (ValueError, TypeError):
                        pass
    return data


def load_yin_data():
    global _yin_cache
    if _yin_cache is not None:
        return _yin_cache
    data_path = os.path.join(os.path.dirname(__file__), 'yin_workbook_data.json')
    with open(data_path, 'r') as f:
        _yin_cache = _normalize_yin_data(json.load(f))
    return _yin_cache

def get_yin_dashboard():
    return load_yin_data().get('dashboard', {})

def get_yin_target():
    return load_yin_data().get('target', {})

def get_yin_market():
    return load_yin_data().get('market_inputs', {})

def get_yin_comps():
    data = load_yin_data()
    return {
        'trading_comps': data.get('trading_comps', []),
        'precedent_transactions': data.get('precedent_transactions', []),
    }

def get_yin_forecast():
    data = load_yin_data()
    return {
        'assumptions': data.get('assumptions', []),
        'forecast': data.get('forecast', []),
    }

def get_yin_valuation():
    return load_yin_data().get('pe_valuation', [])

def get_yin_lbo():
    return load_yin_data().get('lbo', [])

def get_yin_checks():
    data = load_yin_data()
    return {
        'checks': data.get('checks', []),
        'overall_status': data.get('overall_status', ''),
    }

def get_yin_full():
    return load_yin_data()
