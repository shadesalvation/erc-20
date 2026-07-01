#!/usr/bin/env python3
from __future__ import annotations
from s_seir_model import FunctionUnit, VariableInfo
class TypeEnv:
    def __init__(self, unit: FunctionUnit):
        self.unit=unit; self.variables={v.name:v for v in unit.parameters+unit.returns+unit.locals if v.name}
    def lookup(self,name:str)->VariableInfo|None: return self.variables.get(name)
    def is_bytes_memory(self,name:str)->bool:
        v=self.lookup(name)
        if not v: return False
        t=v.type_string.replace('contract ','')
        return ('bytes memory' in t or 'string memory' in t or (t in {'bytes','string'} and v.data_location=='memory'))
    def is_named_return(self,name:str)->bool: return any(v.name==name for v in self.unit.returns)
    def variables_dict(self): return {k:v.to_dict() for k,v in self.variables.items()}
