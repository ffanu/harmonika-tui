"""Mechanical environment check without PowerShell inline quoting ambiguity."""
import json
import torch
assert torch.cuda.is_available(), 'cuda_unavailable'
print(json.dumps({'torch':torch.__version__,'cuda':torch.version.cuda,'gpu':torch.cuda.get_device_name(0),'available':True}))
