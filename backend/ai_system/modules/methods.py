"""
数学建模方法库（参考 MM-Agent 的 HMML 思路做的精简版）

每个方法记录适用场景、需要的数据、典型步骤和常见坑。
建模阶段先按子问题检索几种候选方法，再让模型基于这些方法写候选方案，
比让模型凭空想方法更稳定，也方便评审时对照"方法是否用对"。
"""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class Method:
    key: str
    name: str
    category: str
    use_when: str
    needs: str
    steps: Tuple[str, ...]
    pitfalls: str

    def brief(self) -> str:
        return (
            f"【{self.name}】({self.category}) 适用：{self.use_when}；数据要求：{self.needs}；"
            f"步骤：{' → '.join(self.steps)}；注意：{self.pitfalls}"
        )


METHODS: List[Method] = [
    Method("model_comparison", "监督学习方法比较", "预测", "比较同一预测任务的基线与候选模型", "带标签的表格数据，CSV或Excel均可",
           ("明确预测目标", "选择符合时间或分组结构的共同拆分", "仅在训练集拟合预处理和模型", "保存逐样本预测并计算共同指标", "解释差异与局限"), "测试集不参与调参；保存拆分和真实输出，实测差异不等于统计显著"),
    # 评价与决策
    Method("ahp", "层次分析法 AHP", "评价决策", "多指标综合评价且指标权重需要专家判断", "判断矩阵（可由文献或数据构造）",
           ("建立层次结构", "构造判断矩阵", "求特征向量得权重", "一致性检验 CR<0.1"), "判断矩阵主观性强，最好与客观赋权结合"),
    Method("entropy_weight", "熵权法", "评价决策", "多指标评价且希望由数据离散程度客观确定权重", "多个对象在多个指标上的数值",
           ("指标正向化与标准化", "计算各指标信息熵", "熵越小权重越大", "加权求综合得分"), "指标方向要统一，零值需平移"),
    Method("topsis", "TOPSIS 优劣解距离法", "评价决策", "对多个方案排序", "方案×指标矩阵和权重",
           ("标准化", "加权", "求正负理想解", "计算贴近度排序"), "常与熵权法/AHP 组合给权重"),
    Method("grey_relational", "灰色关联分析", "评价决策", "样本少、想看各因素与目标序列的关联程度", "参考序列和若干比较序列",
           ("无量纲化", "计算关联系数", "求关联度并排序"), "分辨系数一般取 0.5，结论只表示相似程度不表示因果"),
    Method("fuzzy_eval", "模糊综合评价", "评价决策", "评价等级边界模糊（如优良中差）", "指标隶属度或打分数据",
           ("确定因素集与评语集", "构造隶属度矩阵", "合成运算", "按最大隶属度判级"), "隶属函数要说明依据"),
    # 预测
    Method("regression", "多元回归 / 广义线性模型", "预测", "目标与若干解释变量关系较明确，需要可解释性", "带标签的结构化数据",
           ("变量筛选与共线性检查", "拟合模型", "显著性检验", "残差诊断", "预测"), "检查多重共线性和异方差"),
    Method("arima", "ARIMA 时间序列", "预测", "单变量时间序列、有一定平稳性或可差分平稳", "等间隔历史序列（建议 30 个点以上）",
           ("平稳性检验 ADF", "差分", "ACF/PACF 定阶或 AIC 选阶", "残差白噪声检验", "预测"), "季节性用 SARIMA；样本太少时不稳定"),
    Method("grey_gm11", "灰色预测 GM(1,1)", "预测", "数据很少（4~10 个点）且近似指数增长", "短序列",
           ("累加生成", "最小二乘求参数", "还原预测", "后验差检验"), "只适合短期、单调序列"),
    Method("gbdt", "梯度提升树 / 随机森林", "预测", "特征多、关系非线性、追求精度", "较多带标签样本",
           ("划分训练/测试集", "交叉验证调参", "评估指标", "特征重要性/SHAP 解释"), "要防止数据泄漏，报告测试集指标"),
    Method("logistic", "逻辑回归 / 分类模型", "预测", "二分类或概率预测，需要解释每个因素的影响", "带类别标签的数据",
           ("特征构造", "拟合", "AUC/准确率评估", "系数解释"), "类别不平衡时看 AUC 与 F1"),
    Method("markov", "马尔可夫链", "预测", "状态之间转移、只依赖当前状态", "状态序列或转移频数",
           ("划分状态", "估计转移矩阵", "求 n 步转移或稳态分布"), "需要检验无后效性"),
    # 优化
    Method("linear_programming", "线性 / 整数规划", "优化", "资源分配、排产、选址等目标和约束都是线性的", "成本、容量、需求等参数",
           ("定义决策变量", "写目标函数", "列约束", "求解器求解（scipy/pulp）", "灵敏度分析"), "整数变量多时注意求解时间"),
    Method("nonlinear_programming", "非线性规划", "优化", "目标或约束是非线性的连续优化", "函数形式和参数",
           ("建立模型", "选择算法（SLSQP 等）", "多初值求解", "验证最优性"), "可能陷入局部最优，需多初值"),
    Method("multi_objective", "多目标规划", "优化", "多个相互冲突的目标需要权衡", "各目标的度量",
           ("建立各目标", "加权和/ε-约束/NSGA-II", "求 Pareto 前沿", "选择折中解"), "权重选择要说明理由"),
    Method("heuristic", "启发式算法（遗传、模拟退火、粒子群）", "优化", "组合优化或问题规模大、难以精确求解", "目标函数可计算",
           ("编码", "设计适应度", "参数设置", "多次运行取统计"), "结果有随机性，需要固定种子并报告多次运行"),
    Method("dynamic_programming", "动态规划", "优化", "多阶段决策、满足最优子结构", "阶段、状态、转移代价",
           ("划分阶段", "定义状态", "写状态转移方程", "递推求解"), "状态维数过高会爆炸"),
    Method("graph_path", "图论（最短路、最小生成树、网络流）", "优化", "路径规划、网络设计、运输调度", "节点与边权数据",
           ("建图", "选算法（Dijkstra/Floyd/Kruskal/最大流）", "求解", "结果可视化"), "注意有向/无向和负权边"),
    # 机理模型
    Method("ode", "微分方程模型", "机理建模", "描述随时间连续变化的过程（人口、传染病、动力学）", "初值和参数（可由数据拟合）",
           ("根据机理列方程", "参数估计", "数值求解 solve_ivp", "与数据对比"), "参数辨识要报告拟合优度"),
    Method("sir", "传染病 SIR/SEIR 模型", "机理建模", "传播、扩散类问题", "感染人数等时间序列",
           ("划分仓室", "列微分方程", "拟合传播率与恢复率", "情景模拟"), "R0 解释要和参数一致"),
    Method("cellular_automata", "元胞自动机 / 仿真", "机理建模", "空间演化、交通流、个体交互", "规则和初始状态",
           ("定义网格与状态", "制定演化规则", "多次仿真", "统计指标"), "规则要有现实依据，多次仿真取均值"),
    Method("monte_carlo", "蒙特卡洛模拟", "机理建模", "含随机因素、需要估计分布或风险", "各随机变量的分布",
           ("确定随机变量分布", "大量抽样", "统计结果分布与置信区间"), "报告样本量与收敛情况"),
    # 统计与数据分析
    Method("hypothesis_test", "假设检验与方差分析", "统计分析", "判断差异或影响是否显著", "分组样本数据",
           ("提出假设", "选择检验（t/卡方/ANOVA/非参数）", "计算 p 值", "结论"), "先检查正态性和方差齐性"),
    Method("pca", "主成分分析 / 因子分析", "统计分析", "指标多且相关，需要降维或提取综合因子", "多指标数值数据",
           ("标准化", "求相关矩阵特征值", "按累计贡献率取主成分", "解释载荷"), "主成分含义需结合载荷解释"),
    Method("clustering", "聚类分析（K-means、层次聚类）", "统计分析", "无标签数据分组、画像", "多维特征数据",
           ("标准化", "选择 k（肘部法/轮廓系数）", "聚类", "解释各类特征"), "K-means 对尺度和初值敏感"),
    Method("correlation", "相关性分析", "统计分析", "初步探索变量之间关系", "成对数值数据",
           ("Pearson/Spearman 相关", "显著性检验", "热力图"), "相关不等于因果"),
]

METHOD_INDEX: Dict[str, Method] = {m.key: m for m in METHODS}


SKILL_SOURCES = {
    'model_comparison': ('scikit-learn 模型评估文档', 'https://scikit-learn.org/stable/modules/model_evaluation.html'),
    'regression': ('statsmodels 线性回归文档', 'https://www.statsmodels.org/stable/regression.html'),
    'arima': ('statsmodels ARIMA 文档', 'https://www.statsmodels.org/stable/generated/statsmodels.tsa.arima.model.ARIMA.html'),
    'linear_programming': ('SciPy linprog 文档', 'https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.linprog.html'),
    'monte_carlo': ('SciPy MonteCarloMethod 文档', 'https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.MonteCarloMethod.html'),
    'pca': ('scikit-learn PCA 文档', 'https://scikit-learn.org/stable/modules/generated/sklearn.decomposition.PCA.html'),
    'clustering': ('scikit-learn 聚类文档', 'https://scikit-learn.org/stable/modules/clustering.html#k-means'),
}
SKILL_VERSION = 2
DEFAULT_SKILL_POLICY = {'mode': 'auto', 'selected': [], 'disabled': []}


def validate_skill_policy(policy):
    if not isinstance(policy, dict) or set(policy) != {'mode', 'selected', 'disabled'} or policy['mode'] not in {'auto', 'selected'}:
        raise ValueError('技能配置格式错误')
    for field in ('selected', 'disabled'):
        values = policy[field]
        if not isinstance(values, list) or any(not isinstance(key, str) or key not in SKILL_SOURCES for key in values) or len(set(values)) != len(values):
            raise ValueError('技能配置包含未知或重复编号')
    if set(policy['selected']) & set(policy['disabled']):
        raise ValueError('同一技能不能同时选用和禁用')
    if policy['mode'] == 'selected' and not policy['selected']:
        raise ValueError('请至少选择一个技能，或切换自动选择')
    return {'mode': policy['mode'], 'selected': sorted(policy['selected']) if policy['mode'] == 'selected' else [],
            'disabled': sorted(policy['disabled'])}


def allowed_method_keys(policy=None):
    policy = policy or DEFAULT_SKILL_POLICY
    return set(policy['selected'] if policy['mode'] == 'selected' else METHOD_INDEX) - set(policy['disabled'])


def skill_catalog():
    # Metadata only. Full instructions are loaded on detail/proposal/solve requests.
    return [{**asdict(METHOD_INDEX[key]), 'version': SKILL_VERSION,
             'sources': [{'title': source[0], 'url': source[1]}], 'origin': 'project_curated'}
            for key, source in SKILL_SOURCES.items()]


def skill_detail(key):
    if key not in SKILL_SOURCES:
        raise ValueError('技能不存在')
    item = next(item for item in skill_catalog() if item['key'] == key)
    return {**item, 'instructions': (Path(__file__).with_name('skills') / (item['key'] + '.md')).read_text(encoding='utf-8')}


def describe(keys: List[str], policy=None) -> str:
    allowed = allowed_method_keys(policy)
    texts = []
    for key in dict.fromkeys(keys):
        if key not in allowed:
            continue
        texts.append(METHOD_INDEX[key].brief())
        if key in SKILL_SOURCES:
            skill = skill_detail(key)
            texts.append(f"技能 {key} v{skill['version']}（项目整理，非本研究经验）\n" + skill['instructions']
                         + '\n参考来源：' + SKILL_SOURCES[key][1])
    return "\n\n".join(texts)
