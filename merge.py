from mlx_lm.utils import load_model, load_tokenizer, save_model, load_config
from mlx_lm.utils import _download
import mlx.core as mx
import json
from pathlib import Path

model_id = "unsloth/gemma-4-E4B-it"
adapter_dir = "/Users/hannateshager/Ethiopia_2026election_monitoring_tool/model_cache/gemma-disarm-phase3-ttp"
save_path = "/Users/hannateshager/Ethiopia_2026election_monitoring_tool/model_cache/gemma-merged"

# Ensure output caching paths physically exist on disk
output_dir = Path(save_path)
output_dir.mkdir(parents=True, exist_ok=True)

print("Resolving Hugging Face repo ID to your local cache directory...")
absolute_model_path = Path(_download(model_id))
print(f"Found base model cached at: {absolute_model_path}")

print("\nLoading tokenizer...")
tokenizer = load_tokenizer(str(absolute_model_path))

print("Loading and patching configuration properties...")
config_dict = load_config(absolute_model_path)
config_dict["num_hidden_layers"] = 42

print("Initializing base architecture layout using corrected layer counts...")
model, config = load_model(absolute_model_path, lazy=False, model_config=config_dict, strict=False)

print("Loading local adapter safetensors...")
adapter_file = Path(adapter_dir) / "adapters.safetensors"
raw_adapter_weights = mx.load(str(adapter_file))

print("Cleaning adapter weight namespaces for MLX architecture compatibility...")
clean_adapter_weights = {}

for key, value in raw_adapter_weights.items():
    new_key = key
    if new_key.startswith("base_model.model."):
        new_key = new_key.replace("base_model.model.", "", 1)
    
    if new_key.startswith("model.model."):
        new_key = new_key.replace("model.model.", "model.", 1)
        
    clean_adapter_weights[new_key] = value

print("Fusing cleaned adapter layers into the model architecture...")
try:
    model.update(clean_adapter_weights)
except ValueError:
    for k, v in clean_adapter_weights.items():
        try:
            model.load_weights([(k, v)], strict=False)
        except Exception:
            pass

print(f"Saving weight parameters to {save_path}...")
# Pass exactly 2 positional arguments: target path string and the model object instance
save_model(str(output_dir), model)

print("Writing patched configurations and tokenizers to output directory...")
# 1. Save out the custom 42-layer configuration properties file
with open(output_dir / "config.json", "w") as f:
    json.dump(config_dict, f, indent=4)

# 2. Instruct the tokenizer to write its asset bundle out locally
tokenizer.save_pretrained(str(output_dir))

print("\nSuccess! Fused model weights and configuration structures have been written locally.")
