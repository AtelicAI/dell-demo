"""Deterministic assumed-input steady skid, independent of event scenarios."""
import json
import math


def solve_process(facility, config=None):
    """Return SI node/edge states, thermodynamics, residuals and sensor predictions.

    Accept a canonical Facility or its dictionary. Invalid/unsupported graphs raise
    ValueError; no silent topology reduction beyond explicitly closed line IDs.
    """
    f = facility.model_dump() if hasattr(facility, 'model_dump') else facility
    from .data import DATA
    c = json.loads((DATA / 'facility/process_model.json').read_text())
    if config:
        if set(config) - set(c):
            raise ValueError('unknown process configuration')
        c.update(config)
    for k, v in c.items():
        if isinstance(v, (float, int)) and (not math.isfinite(v) or v <= 0):
            raise ValueError('nonpositive or nonfinite process input: ' + k)
    if not 0 < c['isentropic_efficiency'] <= 1 or c['heat_capacity_ratio'] <= 1 or c['compressor_pressure_ratio'] <= 1:
        raise ValueError('invalid compressor inputs')
    assets = {a['id']: a for a in f['assets']}
    edges = [e for e in f['connections'] if e['line_id'] not in c['closed_line_ids']]
    order = [c['inlet_asset_id']]
    while order[-1] != c['outlet_asset_id']:
        choices = [e['to_id'] for e in edges if e['from_id'] == order[-1]]
        if len(choices) != 1 or choices[0] in order:
            raise ValueError('expected a single open train')
        order.append(choices[0])
    if len(edges) != len(order)-1 or c['compressor_asset_id'] not in order:
        raise ValueError('unsupported open network')
    m, d, mu = c['mass_flow_kg_s'], c['pipe_diameter_m'], c['dynamic_viscosity_pa_s']
    withdrawals = c['withdrawals_kg_s']
    if not isinstance(withdrawals, dict):
        raise ValueError('withdrawals_kg_s must be a dictionary')
    for aid, value in withdrawals.items():
        if aid not in order or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('withdrawals require open-train IDs and finite nonnegative flows')
    total_withdrawal = math.fsum(withdrawals.values())
    if total_withdrawal >= m:
        raise ValueError('total side withdrawal must be less than inlet flow')
    inlet_flow = m
    outlet_flow = inlet_flow-total_withdrawal
    area = math.pi*d*d/4
    p, temp = c['inlet_pressure_pa'], c['inlet_temperature_k']
    nodes, flows, thermo = {}, [], {}
    for i, aid in enumerate(order):
        if aid == c['compressor_asset_id']:
            suction = p
            ratio = c['compressor_pressure_ratio']
            discharge_t = temp*(1+(ratio**((c['heat_capacity_ratio']-1)/c['heat_capacity_ratio'])-1)/c['isentropic_efficiency'])
            cp = c['heat_capacity_ratio']*c['specific_gas_constant_j_kg_k']/(c['heat_capacity_ratio']-1)
            cooled = c['aftercooler_temperature_k']
            if cooled > discharge_t:
                raise ValueError('aftercooler would require heating')
            thermo = dict(asset_id=aid, inlet_mass_flow_kg_s=m, suction_pressure_pa=suction, discharge_pressure_pa=p*ratio,
                          suction_temperature_k=temp, discharge_temperature_k=discharge_t,
                          aftercooler_temperature_k=cooled, shaft_power_w=m*cp*(discharge_t-temp),
                          cooling_duty_w=m*cp*(discharge_t-cooled))
            p *= ratio
            temp = cooled
        incoming_flow = m
        m -= withdrawals.get(aid, 0.)
        nodes[aid] = dict(pressure_pa=p, temperature_k=temp,
                          inlet_mass_flow_kg_s=incoming_flow, outlet_mass_flow_kg_s=m,
                          side_withdrawal_kg_s=withdrawals.get(aid, 0.))
        if i == len(order)-1:
            break
        other = order[i+1]
        length = max(c['minimum_edge_length_m'], math.dist(assets[aid]['position'], assets[other]['position']))
        minor = c['valve_minor_loss_k'] if assets[other]['kind'] == 'valve' else 0
        re = m*d/(area*mu)
        friction = 64/re if re < 2300 else (-1.8*math.log10((c['roughness_m']/d/3.7)**1.11+6.9/re))**-2
        loss = (friction*length/d+minor)*m*m*c['compressibility_factor']*c['specific_gas_constant_j_kg_k']*temp/area**2
        if p*p <= loss:
            raise ValueError('infeasible boundary flow')
        downstream = math.sqrt(p*p-loss)
        flows.append(dict(from_id=aid, to_id=other, mass_flow_kg_s=m, length_m=length,
                          reynolds_number=re, darcy_friction_factor=friction,
                          pressure_loss_pa=p-downstream, squared_pressure_loss_pa2=loss))
        p = downstream
    for aid in assets.keys()-nodes.keys():
        nodes[aid] = dict(pressure_pa=None, temperature_k=None, status='isolated_closed_branch')
    residuals = {aid: sum(e['mass_flow_kg_s'] for e in flows if e['to_id']==aid)
                 - sum(e['mass_flow_kg_s'] for e in flows if e['from_id']==aid)
                 + (inlet_flow if aid==order[0] else 0)
                 - withdrawals.get(aid, 0.) - (outlet_flow if aid==order[-1] else 0) for aid in assets}
    predictions = []
    for s in f['sensors']:
        n = nodes[s['asset_id']]
        value = {'pressure': None if n['pressure_pa'] is None else n['pressure_pa']/1e5,
                 'temperature': n['temperature_k'], 'flow': n['outlet_mass_flow_kg_s']*3600 if s['asset_id'] in order else 0}.get(s['variable'])
        predictions.append(dict(sensor_id=s['id'], asset_id=s['asset_id'], unit=s['unit'], value=value,
                                status='model_prediction' if value is not None else 'not_modeled'))
    return dict(method='isothermal-darcy-steady-train', version='1.1', status='solved', assumptions=c,
                nodes=nodes, edges=flows, compressor=thermo,
                inlet_mass_flow_kg_s=inlet_flow, outlet_mass_flow_kg_s=outlet_flow,
                total_side_withdrawal_kg_s=total_withdrawal,
                reynolds_number=flows[0]['reynolds_number'],
                darcy_friction_factor=flows[0]['darcy_friction_factor'],
                mass_balance_residual_kg_s=residuals, max_mass_balance_residual_kg_s=max(map(abs,residuals.values())),
                measurements=predictions)
