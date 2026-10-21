"""Build worker methods from the controller's versioned task intent; no planner."""

def experiment_design(plan):
    from ai_system.modules.methods import SKILL_VERSION,SKILL_SOURCES
    candidates=[{'name':c['name'], 'method_keys':c.get('method_keys',[]),
        'idea':c['instruction'], 'formulation':'', 'steps':[c['instruction']],
        'assumptions':[], 'validation':plan['evaluation']} for c in plan['candidates']]
    chosen=candidates[0] if not plan.get('csv') else {
        'name':'共同条件下比较候选', 'idea':'执行已记录候选并保留全部实测结果，由研究控制器决定后续行动。',
        'method_keys':[], 'formulation':'', 'steps':[], 'assumptions':[], 'validation':plan['evaluation']}
    return {'methods':list(dict.fromkeys(k for c in candidates for k in c['method_keys'])),
            'candidates':candidates, 'chosen':chosen, 'chosen_index':None if plan.get('csv') else 0,
            'scores':[], 'experiment':plan, 'controller_selected':True,
            'skills':[{'key':key,'version':SKILL_VERSION} for key in dict.fromkeys(k for c in candidates for k in c['method_keys']) if key in SKILL_SOURCES]}
