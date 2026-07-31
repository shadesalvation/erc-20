#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path as _SSEIRPath
import sys as _sseir_sys
_SSEIR_ROOT = _SSEIRPath(__file__).resolve().parents[1]
for _sseir_path in (_SSEIR_ROOT / "legacy_yul", _SSEIR_ROOT / "s_seir"):
    _sseir_text = str(_sseir_path)
    if _sseir_text not in _sseir_sys.path:
        _sseir_sys.path.insert(0, _sseir_text)
from s_seir_model import FunctionUnit, VariableInfo
class TypeEnv:
    def __init__(self, unit: FunctionUnit):
        self.unit=unit; self.variables={v.name:v for v in unit.parameters+unit.returns+unit.locals+unit.state_variables if v.name}
        self.state_variables={v.name:v for v in unit.state_variables if v.name}
        self.state_slots={str(v.storage_slot):v for v in unit.state_variables if v.storage_slot is not None}
        self.structs=getattr(unit,'struct_definitions',{}) or {}
        self.constants=getattr(unit,'constant_values',{}) or {}
    def lookup(self,name:str)->VariableInfo|None: return self.variables.get(name)
    def constant(self,name:str|None):
        if not name:
            return None
        return self.constants.get(str(name).strip())
    def constant_value(self,name:str|None):
        item=self.constant(name)
        return item.get('value') if item else None
    def is_bool(self,name:str|None)->bool:
        if not name:
            return False
        v=self.lookup(name)
        return bool(v and str(v.type_string).strip() == 'bool')
    def is_bytes_memory(self,name:str)->bool:
        v=self.lookup(name)
        if not v: return False
        t=v.type_string.replace('contract ','')
        return ('bytes memory' in t or 'string memory' in t or (t in {'bytes','string'} and v.data_location=='memory'))
    def is_named_return(self,name:str)->bool: return any(v.name==name for v in self.unit.returns)
    def is_memory_pointer_return(self,name:str)->bool:
        v=self.lookup(name)
        if not v or not self.is_named_return(name):
            return False
        return self.is_memory_struct(v) or self.is_memory_array(v) or v.data_location=='memory'
    def is_memory_struct(self,var:VariableInfo|str|None)->bool:
        v=self.lookup(var) if isinstance(var,str) else var
        if not v:
            return False
        return self.struct_name(v) is not None and (v.data_location=='memory' or ' memory' in v.type_string)
    def is_memory_array(self,var:VariableInfo|str|None)->bool:
        v=self.lookup(var) if isinstance(var,str) else var
        if not v:
            return False
        t=v.type_string
        return '[]' in t and (v.data_location=='memory' or ' memory' in t)
    def is_memory_array_parameter(self,name:str|None)->bool:
        if not name:
            return False
        for v in self.unit.parameters:
            if v.name == name:
                return self.is_memory_array(v)
        return False
    def memory_array_parameter(self,name:str|None)->VariableInfo|None:
        if not name:
            return None
        for v in self.unit.parameters:
            if v.name == name and self.is_memory_array(v):
                return v
        return None
    def memory_array_element_type(self,name:str|None)->str|None:
        v=self.memory_array_parameter(name)
        if not v:
            return None
        text=v.type_string.replace(' memory','').replace(' calldata','').replace(' storage','').strip()
        if text.endswith('[]'):
            return text[:-2]
        return None
    def is_storage_reference(self,var:VariableInfo|str|None)->bool:
        v=self.lookup(var) if isinstance(var,str) else var
        if not v:
            return False
        return v.data_location=='storage' or ' storage' in v.type_string
    def is_mapping_type(self,type_string:str|None)->bool:
        return str(type_string or '').strip().startswith('mapping(')
    def struct_name(self,var:VariableInfo|str|None)->str|None:
        v=self.lookup(var) if isinstance(var,str) else var
        if not v:
            return None
        text=v.type_string.replace('struct ','').replace(' memory','').replace(' storage','').replace(' calldata','').strip()
        if text in self.structs:
            return text
        short=text.split('.')[-1]
        if short in self.structs:
            return short
        return None
    def struct_layout_for_var(self,name:str):
        v=self.lookup(name)
        struct=self.struct_name(v)
        if not struct:
            return None
        return self.structs.get(struct)
    def struct_field_by_offset(self,name:str,offset:int):
        layout=self.struct_layout_for_var(name)
        if not layout:
            return None
        for field in layout.get('fields',[]):
            if int(field.get('offset',-1))==offset:
                return field
        return None
    def struct_field_by_storage_slot_offset(self,name:str,slot_offset:int):
        layout=self.struct_layout_for_var(name)
        if not layout:
            return None
        for field in layout.get('fields',[]):
            try:
                index=int(field.get('index',-1))
            except Exception:
                index=-1
            if index==slot_offset:
                return field
        return None
    def storage_ref_mapping_access(self,root:str,slot_offset:int,key:str):
        v=self.lookup(root)
        if not v or not self.is_storage_reference(v):
            return None
        if self.is_mapping_type(v.type_string) and slot_offset==0:
            return {'root':root,'root_type':v.type_string,'mapping_type':v.type_string,'field':None,'access':f'{root}[{key}]','slot_offset':slot_offset,'kind':'mapping_storage_ref'}
        field=self.struct_field_by_storage_slot_offset(root,slot_offset)
        if not field or not self.is_mapping_type(field.get('type_string')):
            return None
        name=field.get('name')
        return {'root':root,'root_type':v.type_string,'mapping_type':field.get('type_string'),'field':field,'access':f'{root}.{name}[{key}]','slot_offset':slot_offset,'kind':'struct_mapping_storage_ref'}
    def state_var_by_slot(self,slot:str):
        text=str(slot).strip()
        if text.endswith('.slot'):
            return self.state_variables.get(text[:-5])
        try:
            text=str(int(text,0))
        except Exception:
            pass
        return self.state_slots.get(text)
    def variables_dict(self): return {k:v.to_dict() for k,v in self.variables.items()}
