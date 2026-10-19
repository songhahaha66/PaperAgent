"""Narrow executable contracts for explicitly named sklearn linear candidates.

Other model families remain outside this semantic checker. This validates the
actual estimator object, not a model's description or self-reported algorithm.
"""
import re

VERSION=1


def candidate_contract(candidate):
    name=str(candidate.get('name',''))
    text=' '.join([name,str(candidate.get('idea','')),str(candidate.get('formulation','')),*candidate.get('steps',[])])
    ols=re.search(r'\bOLS\b|LinearRegression|最小二乘',name,re.I)
    ridge=re.search(r'Ridge|岭回归',name,re.I)
    if not ols and not ridge:
        ols=re.search(r'\bOLS\b|LinearRegression|最小二乘',text,re.I)
        ridge=re.search(r'Ridge|岭回归',text,re.I)
    if bool(ols)==bool(ridge):return None
    alpha=re.search(r'(?:alpha|α|\\alpha)\s*(?:=|为|:|：)\s*([0-9]+(?:\.[0-9]+)?)',text,re.I)
    return {'family':'ols' if ols else 'ridge','alpha':float(alpha.group(1)) if ridge and alpha else None,
            'standardize':bool(re.search(r'StandardScaler|标准化|\bscaled\b',text,re.I))}


def check_estimator(estimator,contract):
    """Self-contained: also embedded in the frozen project kernel driver."""
    if not contract:return {'checked':False,'reason':'该模型家族尚无项目方法契约'}
    steps=estimator.steps if type(estimator).__name__=='Pipeline' else [('model',estimator)]
    preprocess=[type(model).__name__ for _,model in steps[:-1]]
    final=steps[-1][1]
    expected='LinearRegression' if contract['family']=='ols' else 'Ridge'
    if type(final).__name__!=expected or not type(final).__module__.startswith('sklearn.'):
        raise ValueError('候选方法契约不符：实际估计器必须为 '+expected)
    if any(name!='StandardScaler' for name in preprocess):
        raise ValueError('候选方法契约不符：不允许未声明的特征扩展或额外估计器')
    if contract['standardize'] and 'StandardScaler' not in preprocess:
        raise ValueError('候选方法契约不符：缺少训练集 StandardScaler')
    parameters=final.get_params(deep=False)
    if not parameters.get('fit_intercept',True):
        raise ValueError('候选方法契约不符：普通线性模型不得擅自移除截距')
    if contract['alpha'] is not None and abs(float(parameters.get('alpha',float('nan')))-contract['alpha'])>1e-12:
        raise ValueError('候选方法契约不符：Ridge alpha 与确认方案不同')
    return {'checked':True,'family':contract['family'],'estimator':type(final).__module__+'.'+type(final).__name__,
            'preprocessors':preprocess,'alpha':parameters.get('alpha'),'fit_intercept':parameters.get('fit_intercept')}
