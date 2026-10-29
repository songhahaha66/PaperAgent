"""Recompute explicit linear constraints from decisions, without solver-reported residuals."""
import math


def verify_linear(model, values, objective, tolerance):
    failures = []
    for variable, value in zip(model.variables, values, strict=True):
        if variable.lower is not None and value < variable.lower - tolerance:
            failures.append(f"{variable.name}={value:g} 低于下界 {variable.lower:g}")
        if variable.upper is not None and value > variable.upper + tolerance:
            failures.append(f"{variable.name}={value:g} 超过上界 {variable.upper:g}")
        if variable.integer and abs(value - round(value)) > tolerance:
            failures.append(f"{variable.name}={value:g} 不满足整数约束")
    for index, constraint in enumerate(model.constraints, 1):
        lhs = math.fsum(a * x for a, x in zip(constraint.coefficients, values, strict=True))
        if not math.isfinite(lhs):
            raise ValueError("线性约束复算溢出")
        passed = {"le": lhs <= constraint.rhs + tolerance,
                  "ge": lhs >= constraint.rhs - tolerance,
                  "eq": abs(lhs - constraint.rhs) <= tolerance}[constraint.relation]
        if not passed:
            failures.append(f"约束{index}（{constraint.source_quote}）复算左值 {lhs:g}，不满足 {constraint.relation} {constraint.rhs:g}")
    computed = math.fsum([model.offset, *(a * x for a, x in zip(model.objective, values, strict=True))])
    if not math.isfinite(computed):
        raise ValueError("目标值复算溢出")
    if abs(computed - objective) > tolerance:
        failures.append(f"目标值登记为 {objective:g}，由决策变量复算为 {computed:g}")
    return not failures, "；".join(failures) if failures else f"约束、边界和整数性通过，目标值复算为 {computed:g}；未验证最优性"
