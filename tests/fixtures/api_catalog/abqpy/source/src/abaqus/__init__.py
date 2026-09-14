import builtins

builtins.ABQPY_AST_FIXTURE_EXECUTED = True
raise RuntimeError("the abqpy AST fixture must never be imported")
