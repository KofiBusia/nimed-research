"""
Computation engine — emulates the Excel workbook formulas.
Inputs + Financial Data + Prices + Betas → Dashboard metrics.
"""
import json
import os
import copy
from datetime import datetime


def _safe(v, default=0):
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def load_base_data():
    path = os.path.join(os.path.dirname(__file__), 'workbook_data.json')
    with open(path, 'r') as f:
        return json.load(f)


def get_active_inputs(db_inputs, json_inputs):
    merged = dict(json_inputs)
    for mi in db_inputs:
        if mi.input_type in ('select',):
            if mi.text_value:
                merged[mi.key] = mi.text_value
        elif mi.value is not None:
            merged[mi.key] = mi.value
    return merged


def extract_key_metrics(items, periods):
    metrics = {}
    latest_fy = None
    for p in reversed(periods):
        if p.startswith('FY') and any(
            item.get(p) is not None and item.get(p) != 0
            for item in items if item.get('stmt') in ('IS', 'BS', '')
        ):
            latest_fy = p
            break
    if not latest_fy:
        return metrics
    metrics['latest_fy'] = latest_fy

    label_map = {}
    for item in items:
        label = (item.get('item') or '').strip()
        label_map[label.lower()] = item

    def val(label_fragment, period=None):
        period = period or latest_fy
        for lbl, item in label_map.items():
            if label_fragment.lower() in lbl:
                v = item.get(period)
                if v is not None:
                    return _safe(v)
        return None

    metrics['net_income'] = val('net income') or val('net profit attributable') or val('profit after tax')
    metrics['total_equity'] = val('total equity')
    metrics['total_assets'] = val('total assets')
    metrics['operating_income'] = val('operating income')
    metrics['operating_expenses'] = val('operating expenses')
    metrics['net_interest_income'] = val('net interest income')
    metrics['customer_deposits'] = val('customer deposits')
    metrics['loans_advances'] = val('loans and advances')

    for item in items:
        label = (item.get('item') or '').lower()
        if 'eps' in label and 'ghs' in label:
            v = item.get(latest_fy)
            if v is not None:
                metrics['eps'] = _safe(v)
        elif label.startswith('dps') or 'dividend per share' in label:
            v = item.get(latest_fy)
            if v is not None:
                metrics['dps'] = _safe(v)
        elif item.get('stmt') == 'META':
            if 'car' in label or 'capital adequacy' in label:
                v = item.get(latest_fy)
                if v is not None:
                    metrics['car'] = _safe(v)
            elif 'npl' in label:
                v = item.get(latest_fy)
                if v is not None:
                    metrics['npl'] = _safe(v)
    return metrics


def compute_cost_of_equity(beta_used, risk_free_rate, erp):
    return risk_free_rate + beta_used * erp


def compute_shareholder_ratios(ticker, company, sector, metrics, price, shares_m, ke, inputs):
    eps = metrics.get('eps', 0)
    dps = metrics.get('dps', 0)
    net_income = metrics.get('net_income')
    total_equity = metrics.get('total_equity')
    total_assets = metrics.get('total_assets')
    bvps = (total_equity / (shares_m * 1e6) * 1000) if total_equity and shares_m else None

    pe = price / eps if eps and eps > 0 else None
    pb = price / bvps if bvps and bvps > 0 else None
    div_yield = dps / price if price and dps else None
    earn_yield = eps / price if price and eps else None
    roe = (net_income / total_equity) if net_income and total_equity and total_equity > 0 else None
    roa = (net_income / total_assets) if net_income and total_assets and total_assets > 0 else None
    payout = dps / eps if eps and eps > 0 and dps else None
    retention = 1 - payout if payout is not None else None
    sust_growth = roe * retention if roe is not None and retention is not None else None
    mkt_cap = price * shares_m if price and shares_m else None

    return {
        'Ticker': ticker,
        'Company': company,
        'Sector': sector,
        'Latest FY': metrics.get('latest_fy', ''),
        'Price (GHS)': price,
        'Shares (m)': shares_m,
        'Market cap (GHS m)': mkt_cap,
        'EPS': eps,
        'DPS (latest FY)': dps,
        'Book value per share': bvps,
        'P/E': pe,
        'P/B': pb,
        'Earnings yield': earn_yield,
        'Dividend yield': div_yield,
        'ROE (latest)': roe,
        'ROA': roa,
        'Payout ratio': payout,
        'Retention ratio': retention,
        'Sustainable growth (5-yr ROE x retention)': sust_growth,
        'Cost of equity': ke,
    }


def compute_valuation_models(metrics, inputs, beta_used, sector, sector_weights, peer_medians):
    ke = compute_cost_of_equity(
        beta_used, inputs.get('risk_free_rate', 0.111), inputs.get('equity_risk_premium', 0.07)
    )
    g = inputs.get('long_term_growth', 0.064)
    eps = metrics.get('eps', 0)
    dps = metrics.get('dps', 0)
    net_income = metrics.get('net_income')
    total_equity = metrics.get('total_equity')
    total_assets = metrics.get('total_assets')
    shares_m = metrics.get('shares_m', 0)

    bvps = (total_equity / (shares_m * 1e6) * 1000) if total_equity and shares_m else 0
    roe = (net_income / total_equity) if net_income and total_equity and total_equity > 0 else 0
    payout = dps / eps if eps and eps > 0 and dps else 0
    retention = 1 - payout if payout else 1

    models = {}
    sw = {}
    for w in sector_weights:
        if w.get('sector', '').lower() == sector.lower():
            sw = w
            break

    if ke <= g or ke <= 0:
        return models, ke

    # Comparable companies
    med_pe = peer_medians.get('pe')
    med_pb = peer_medians.get('pb')
    if med_pe and eps > 0:
        v_comps_pe = eps * med_pe
    else:
        v_comps_pe = None
    if med_pb and bvps > 0:
        v_comps_pb = bvps * med_pb
    else:
        v_comps_pb = None
    if v_comps_pe and v_comps_pb:
        models['comps'] = (v_comps_pe + v_comps_pb) / 2
    elif v_comps_pe:
        models['comps'] = v_comps_pe
    elif v_comps_pb:
        models['comps'] = v_comps_pb

    # Justified multiples
    sust_g = roe * retention if roe and retention else g
    if ke > sust_g and sust_g > 0:
        just_pe = payout / (ke - sust_g) if payout else None
        just_pb = (roe - sust_g) / (ke - sust_g) if roe else None
        v_just = []
        if just_pe and eps > 0:
            v_just.append(eps * just_pe)
        if just_pb and bvps > 0:
            v_just.append(bvps * just_pb)
        if v_just:
            models['justified'] = sum(v_just) / len(v_just)

    # Residual Income
    omega = 0.6
    if bvps > 0 and roe:
        ri = (roe - ke) * bvps
        denom = 1 + ke
        pv_ri = ri / denom
        tv_num = omega * ri
        tv_den = (ke - g * omega) if (ke - g * omega) > 0 else 0.01
        tv = tv_num / tv_den
        pv_tv = tv / denom
        models['ri'] = bvps + pv_ri + pv_tv

    # DDM 2-stage
    if dps > 0 and ke > 0:
        g1 = sust_g if sust_g and sust_g > 0 else g
        if g1 > ke:
            g1 = ke * 0.9
        pv_divs = 0
        d = dps
        for t in range(1, 6):
            d = d * (1 + g1)
            pv_divs += d / (1 + ke) ** t
        d_terminal = d * (1 + g)
        tv = d_terminal / (ke - g)
        pv_tv = tv / (1 + ke) ** 5
        models['ddm2'] = pv_divs + pv_tv

    # DDM 3-stage
    if dps > 0 and ke > 0:
        g1 = sust_g if sust_g and sust_g > 0 else g
        if g1 > ke:
            g1 = ke * 0.9
        pv = 0
        d = dps
        for t in range(1, 6):
            d = d * (1 + g1)
            pv += d / (1 + ke) ** t
        for t in range(6, 11):
            fade = g1 + (g - g1) * (t - 5) / 5
            d = d * (1 + fade)
            pv += d / (1 + ke) ** t
        d_terminal = d * (1 + g)
        tv = d_terminal / (ke - g)
        pv += tv / (1 + ke) ** 10
        models['ddm3'] = pv

    # Weighted value
    weight_map = {
        'ddm2': sw.get('ddm2', 0.125),
        'ddm3': sw.get('ddm3', 0.125),
        'ri': sw.get('ri', 0.25),
        'comps': sw.get('comps', 0.125),
        'justified': sw.get('justified', 0.1),
    }
    total_w = sum(weight_map.get(k, 0) for k in models if k in weight_map)
    if total_w > 0:
        weighted = sum(models[k] * weight_map.get(k, 0) for k in models if k in weight_map)
        models['weighted_value'] = weighted / total_w

    return models, ke


def compute_credit_score(bank_metrics, scoring_params):
    ratios = {
        'Capital adequacy ratio': bank_metrics.get('car', 0),
        'NPL ratio': bank_metrics.get('npl', 0),
        'Return on equity': bank_metrics.get('roe', 0),
        'Loan-to-deposit ratio': (
            bank_metrics.get('loans_advances', 0) / bank_metrics.get('customer_deposits', 1)
            if bank_metrics.get('customer_deposits') else 0
        ),
    }
    score = 0
    total_weight = 0
    for sp in scoring_params:
        ratio_name = sp.get('ratio', '')
        weight = _safe(sp.get('weight', 0))
        strong = _safe(sp.get('strong_if', 0))
        weak = _safe(sp.get('weak_if', 0))
        better = sp.get('better_when', 'Higher')

        actual = None
        for rk, rv in ratios.items():
            if ratio_name.lower() in rk.lower():
                actual = rv
                break
        if actual is None:
            continue

        if better == 'Higher':
            if actual >= strong:
                s = 1.0
            elif actual <= weak:
                s = 0.0
            else:
                s = (actual - weak) / (strong - weak) if strong != weak else 0.5
        else:
            if actual <= strong:
                s = 1.0
            elif actual >= weak:
                s = 0.0
            else:
                s = (weak - actual) / (weak - strong) if weak != strong else 0.5
        score += s * weight
        total_weight += weight

    final_score = score / total_weight * 5 if total_weight > 0 else 0
    if final_score >= 3.75:
        rating = 'A'
    elif final_score >= 2.5:
        rating = 'B'
    else:
        rating = 'C'
    return final_score, rating


def recalculate_all(inputs, base_data, db_prices, db_financials):
    data = copy.deepcopy(base_data)
    betas = data.get('betas', [])
    sector_weights = data.get('sector_weights', [])
    scoring_params = data.get('credit_scoring', [])

    price_map = {}
    for sp in db_prices:
        price_map[sp.ticker] = {
            'price_ghs': sp.price_ghs,
            'shares_m': sp.shares_m,
            'company_name': sp.company_name,
            'source': sp.source,
            'date': sp.price_date,
        }

    fs_map = {}
    pc_map = {}
    for cf in db_financials:
        d = cf.get_data()
        d['company_name'] = cf.company_name or cf.ticker
        d['currency'] = getattr(cf, 'currency', None) or 'GHS'
        d['is_private'] = getattr(cf, 'is_private', False) or False
        d['industry'] = getattr(cf, 'industry', None) or ''
        if d['is_private']:
            pc_map[cf.ticker] = d
        else:
            fs_map[cf.ticker] = d

    fs_data = data.get('financial_statements', {})
    for ticker, cf_data in fs_map.items():
        fs_data[ticker] = cf_data
    data['financial_statements'] = fs_data
    data['pc_companies'] = pc_map

    beta_map = {}
    for b in betas:
        t = b.get('ticker', '')
        beta_used = _safe(b.get('beta_used', 1))
        ke = compute_cost_of_equity(
            beta_used, inputs.get('risk_free_rate', 0.111), inputs.get('equity_risk_premium', 0.07)
        )
        b['cost_of_equity'] = ke
        beta_map[t] = b

    usd_rate = inputs.get('usd_ghs_rate', 11.68)
    band = inputs.get('undervalued_band', 0.15)

    all_peers = data.get('all_peers', {})
    peer_pe_by_sector = {}
    peer_pb_by_sector = {}
    for sector_name, peers in all_peers.items():
        pes = [_safe(p.get('P/E')) for p in peers if p.get('P/E') and _safe(p.get('P/E')) > 0]
        pbs = [_safe(p.get('P/B')) for p in peers if p.get('P/B') and _safe(p.get('P/B')) > 0]
        if pes:
            peer_pe_by_sector[sector_name] = sorted(pes)[len(pes) // 2]
        if pbs:
            peer_pb_by_sector[sector_name] = sorted(pbs)[len(pbs) // 2]

    new_valuations = []
    new_shareholder_ratios = []
    new_peers = {}

    for ticker, company_data in fs_data.items():
        items = company_data.get('items', [])
        periods = company_data.get('periods', [])
        company_name = company_data.get('company_name', ticker)

        pm = price_map.get(ticker)
        beta_info = beta_map.get(ticker, {})
        if not beta_info:
            continue

        sector = beta_info.get('category', 'Other')
        beta_used = _safe(beta_info.get('beta_used', 1))
        ke = _safe(beta_info.get('cost_of_equity', 0.2))

        if pm:
            price = pm['price_ghs']
            shares_m = pm.get('shares_m') or _safe(beta_info.get('shares_m', 0))
        else:
            existing_peer = None
            for s_name, s_peers in all_peers.items():
                for p in s_peers:
                    if p.get('Ticker') == ticker:
                        existing_peer = p
                        break
            if existing_peer:
                price = _safe(existing_peer.get('Last close (GHS)', 0))
                shares_m = _safe(existing_peer.get('Shares (m)', 0))
            else:
                continue

        metrics = extract_key_metrics(items, periods)
        if not metrics:
            continue
        metrics['shares_m'] = shares_m

        sr = compute_shareholder_ratios(ticker, company_name, sector, metrics, price, shares_m, ke, inputs)
        new_shareholder_ratios.append(sr)

        peer_meds = {
            'pe': peer_pe_by_sector.get(sector),
            'pb': peer_pb_by_sector.get(sector),
        }
        models, ke = compute_valuation_models(metrics, inputs, beta_used, sector, sector_weights, peer_meds)

        intrinsic = models.get('weighted_value')
        if intrinsic and intrinsic > 0:
            upside = (intrinsic - price) / price
            if upside > band:
                verdict = 'Undervalued'
            elif upside < -band:
                verdict = 'Overvalued'
            else:
                verdict = 'Fair Value'
            new_valuations.append({
                'ticker': ticker,
                'company': company_name,
                'price_ghs': price,
                'price_usd': price / usd_rate if usd_rate else 0,
                'value_ghs': intrinsic,
                'value_usd': intrinsic / usd_rate if usd_rate else 0,
                'upside': upside,
                'verdict': verdict,
            })

        if sector not in new_peers:
            new_peers[sector] = []
        new_peers[sector].append({
            'Ticker': ticker,
            'Company': company_name,
            'Last close (GHS)': price,
            'Shares (m)': shares_m,
            'Market cap (GHS m)': price * shares_m if shares_m else None,
            'EPS (GHS)': metrics.get('eps'),
            'DPS (GHS)': metrics.get('dps'),
            'P/E': sr.get('P/E'),
            'P/B': sr.get('P/B'),
            'Dividend yield': sr.get('Dividend yield'),
            'Cost of equity': ke,
            'Beta used': beta_used,
            'Sector': sector,
        })

    if new_valuations:
        data['valuations'] = sorted(new_valuations, key=lambda x: x['company'])

    if new_shareholder_ratios:
        data['shareholder_ratios'] = new_shareholder_ratios

    if new_peers:
        for sector_name, peers in new_peers.items():
            data.setdefault('all_peers', {})[sector_name] = peers

    # Update at-a-glance
    aag = data.get('at_a_glance', {})
    vals = data.get('valuations', [])
    n_under = sum(1 for v in vals if v.get('verdict') == 'Undervalued')
    n_fair = sum(1 for v in vals if v.get('verdict') == 'Fair Value')
    n_over = sum(1 for v in vals if v.get('verdict') == 'Overvalued')
    aag['Undervalued / fair / overvalued'] = {
        'value': f'{n_under} / {n_fair} / {n_over}', 'ytd': None
    }
    kes = [_safe(b.get('cost_of_equity')) for b in betas if b.get('cost_of_equity')]
    if kes:
        kes_sorted = sorted(kes)
        aag['Median cost of equity (GSE)'] = {
            'value': kes_sorted[len(kes_sorted) // 2], 'ytd': None
        }
    n_total = len(fs_data)
    n_with_fs = sum(1 for t, d in fs_data.items() if d.get('items'))
    aag['Securities / with statements'] = {'value': f'{n_total} / {n_with_fs}', 'ytd': None}
    if db_prices:
        latest = max(db_prices, key=lambda x: x.updated_at)
        aag['Prices up to'] = {'value': latest.price_date or latest.updated_at.strftime('%Y-%m-%d'), 'ytd': None}
    data['at_a_glance'] = aag

    data['_sources'] = {
        'at_a_glance': 'Computed from market inputs, betas, and financial statements',
        'valuations': f'Multi-model valuation: DDM, RI, Comps, Justified (inputs as of {datetime.utcnow().strftime("%Y-%m-%d")})',
        'betas': f'Beta model: {inputs.get("beta_type", "Dimson + Blume")}, {inputs.get("return_frequency", "Weekly")} returns',
        'credit_risk': 'Weighted credit scoring model (see Credit Risk page for methodology)',
        'shareholder_ratios': 'Computed from latest financial statements and market prices',
        'nimed_index': 'Market-cap weighted, chain-linked daily index',
        'inputs': f'Last updated: {datetime.utcnow().strftime("%Y-%m-%d %H:%M")}',
    }

    return data
