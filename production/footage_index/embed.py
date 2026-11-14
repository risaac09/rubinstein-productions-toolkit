"""SigLIP 2 image and text embeddings, on the Apple GPU (mps) when there is one, L2-normalised."""
import torch
from PIL import Image

from . import config

MODEL = config.get("siglip")
_m = {}


def _load():
    if not _m:
        from transformers import AutoModel, AutoProcessor
        dev = config.get("siglip_device") or ("mps" if torch.backends.mps.is_available() else "cpu")
        _m["model"] = AutoModel.from_pretrained(MODEL).to(dev).eval()
        _m["proc"] = AutoProcessor.from_pretrained(MODEL)
        _m["dev"] = dev
    return _m["model"], _m["proc"], _m["dev"]


def _vec(out):
    # transformers 5 returns a pooled output object where 4.x returned the tensor
    return out if torch.is_tensor(out) else out.pooler_output


@torch.no_grad()
def images(paths):
    model, proc, dev = _load()
    ims = [Image.open(p).convert("RGB") for p in paths]
    x = proc(images=ims, return_tensors="pt").to(dev)
    v = _vec(model.get_image_features(**x))
    return torch.nn.functional.normalize(v, dim=-1).float().cpu().numpy()


@torch.no_grad()
def text(query):
    model, proc, dev = _load()
    x = proc(text=[query.lower()], padding="max_length", max_length=64, return_tensors="pt").to(dev)
    v = _vec(model.get_text_features(**x))
    return torch.nn.functional.normalize(v, dim=-1).float().cpu().numpy()[0]
