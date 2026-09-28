"""既有修改/修复种子的来源合同与独立交互试点输入；不注册从零Gold测评。"""

PLATE_SOURCE_GOAL = """
请在当前空工作目录中，使用本机 Abaqus 2024 从零建立并完成一个二维单轴拉伸分析。

模型是 100 mm × 20 mm 的矩形平面应力板，单位制为 mm-N-MPa。材料为各向同性线弹性，
弹性模量 E=210000 MPa，泊松比 ν=0.3。使用 Abaqus/Standard 的 Static, General，
NLGEOM=OFF；使用 CPS4R，沿长度 20 个单元、沿高度 4 个单元的结构化网格。

左边界 U1=0，左下角点 U2=0；右边界规定 U1=0.1 mm。请求 U、S、RF。
建模脚本必须命名为 build_model.py，并且只生成 plate_tension.cae 与 plate_tension.inp，不在 CAE 脚本中
提交 Job；随后通过独立 Solver 阶段生成 plate_tension.odb，再用命名为 extract_results.py 的独立
odbAccess 脚本生成 results.json。分析步名称可自行选择，但只能创建一个分析步。
不要猜测缺失的物理参数；只有在结果产物和运行证据齐全后才请求验证。
""".strip()

BEAM_SOURCE_GOAL = """
请在当前空工作目录中，使用本机 Abaqus 2024 从零建立并完成一个三维梁单轴拉伸分析。

模型是一条沿全局 X 方向、从 (0,0,0) 到 (100,0,0) mm 的三维 wire，单位制为 mm-N-MPa。
截面为 10×10 mm 的矩形梁截面，材料为各向同性线弹性，弹性模量 E=210000 MPa，泊松比
ν=0.3；必须给出有效且与梁轴线不平行的梁截面方向。使用 Abaqus/Standard 的
Static, General，NLGEOM=OFF；使用 B31，沿长度均匀划分 20 个单元，共 21 个节点。

左端点使用 Encastre，约束全部 6 个自由度；右端点只规定 U1=0.1 mm，其余自由度不额外
约束。请求 U、RF、SF。

建模脚本必须命名为 build_model.py，并且只生成 beam_tension.cae 与 beam_tension.inp，不在
CAE 脚本中提交 Job；随后通过独立 Solver 阶段生成 beam_tension.odb，再用命名为
extract_results.py 的独立 odbAccess 脚本生成 results.json。results.json 必须包含数值字段
right_mean_u1、left_sum_rf1 和 mean_sf1。分析步名称可自行选择，但只能创建一个分析步。
本任务的物理输入已经完整，不需要询问补充信息；只有在结果产物和运行证据齐全后才请求验证。
""".strip()

BAR_REQUIREMENTS = """
请在当前空工作目录中，使用本机 Abaqus 2024 从零建立并完成一个三维矩形杆单轴拉伸分析。

模型是沿 X 方向长 100 mm、Y 和 Z 方向均为 10 mm 的三维实体，单位制为 mm-N-MPa。
材料为各向同性线弹性，弹性模量 E=210000 MPa，泊松比 ν=0.3。使用 Abaqus/Standard 的
Static, General，NLGEOM=OFF；使用 C3D8R，沿长度 20 个单元、两个横向各 2 个单元的
结构化网格，共 80 个单元。

左端面 U1=0；在左端角点 A=(0,0,0) 设置 U2=U3=0，并在左端角点
B=(0,10,0) 设置 U3=0，以最少约束消除其余刚体运动而允许泊松收缩。右端面规定
U1=0.1 mm。请求 U、S、RF。

建模脚本必须命名为 build_model.py，并且只生成 bar_tension.cae 与 bar_tension.inp，不在
CAE 脚本中提交 Job；随后通过独立 Solver 阶段生成 bar_tension.odb，再用命名为
extract_results.py 的独立 odbAccess 脚本生成 results.json。results.json 必须包含数值字段
right_mean_u1、mean_s11 和 left_sum_rf1。分析步名称可自行选择，但只能创建一个分析步。
本任务的物理输入已经完整，不需要询问补充信息；只有在结果产物和运行证据齐全后才请求验证。
""".strip()
