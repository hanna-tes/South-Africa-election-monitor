# TEI model files

These are the standard Hugging Face files used by the local TEI sidecar:

- `runtime/ethiopia-afro-xlmr-hateguard-v7/config.json`
- `runtime/ethiopia-afro-xlmr-hateguard-v7/model.safetensors`
- `runtime/ethiopia-afro-xlmr-hateguard-v7/tokenizer.json`
- `runtime/ethiopia-afro-xlmr-hateguard-v7/tokenizer_config.json`

The large `model.safetensors` file is stored with Git LFS. The original ZIP,
custom PyTorch checkpoint, and training artifacts are not needed by TEI and are
not copied into this repository.
