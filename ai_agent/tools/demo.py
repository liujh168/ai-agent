"""演示类工具：天气、计算器"""

import ast
import operator


def get_weather(city: str = "", location: str = "") -> str:
    """模拟查询天气的工具。兼容模型偶尔传入的 location 别名。"""
    target = city or location
    if not target:
        return "请提供城市名称（参数 city 或 location）"
    weather_data = {
        "北京": "晴天，25°C，湿度 40%",
        "上海": "多云，28°C，湿度 65%",
        "深圳": "阵雨，30°C，湿度 80%",
        "广州": "雷阵雨，32°C，湿度 85%",
    }
    return weather_data.get(target, f"{target}：暂无天气数据")


def calculator(expression: str) -> str:
    """
    执行简单数学计算（AST 白名单，杜绝 eval 任意代码执行）。
    支持整数/浮点数 + - * / ** % 运算，以及括号和一元负号。
    """
    if len(expression) > 200:
        return "计算错误: 表达式过长（最多 200 字符）"

    _ALLOWED_OPS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.Mod: operator.mod,
        ast.USub: operator.neg,
    }

    def _safe_eval(node):
        if isinstance(node, ast.Expression):
            return _safe_eval(node.body)
        if isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in _ALLOWED_OPS:
                raise ValueError(f"不支持的运算符: {op_type.__name__}")
            left = _safe_eval(node.left)
            right = _safe_eval(node.right)
            if op_type is ast.Pow and abs(right) > 1000:
                raise ValueError("指数绝对值不能超过 1000")
            return _ALLOWED_OPS[op_type](left, right)
        if isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type not in _ALLOWED_OPS:
                raise ValueError(f"不支持的一元运算符: {op_type.__name__}")
            return _ALLOWED_OPS[op_type](_safe_eval(node.operand))
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        # 以下类型一律拒绝：Name, Call, Attribute, Subscript, Lambda, ...
        raise ValueError(f"不支持的表达式元素: {type(node).__name__}")

    try:
        tree = ast.parse(expression, mode="eval")
        return str(_safe_eval(tree))
    except SyntaxError as e:
        return f"语法错误: {e}"
    except Exception as e:
        return f"计算错误: {e}"
