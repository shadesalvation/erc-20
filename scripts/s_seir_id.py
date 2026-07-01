#!/usr/bin/env python3
class IdAllocator:
    def __init__(self): self.counts={}
    def new(self,prefix:str)->str:
        self.counts[prefix]=self.counts.get(prefix,0)+1
        return f"{prefix}_{self.counts[prefix]}"
