# ITS Level of Traffic Stress
## Setup
Using uv, follow [set up instructions](https://docs.astral.sh/uv/getting-started/installation/)

```bash
# Install dependencies
uv sync --locked

# Set up ipykernel for jupyter env, when running jupyter notebook set "LTS_CV (Polaris)" as the kernel 
uv run python -m ipykernel install --user \
    --name lts-cv \
    --display-name "LTS_CV (Polaris)"
```

## Jupyter Coding Agent
```bash
# Set up jupter.ai
python -m pip install --user "jupyter-ai==3.2.0"

# Set up npm to install global packages to home dir
mkdir -p "$HOME/.local/bin"
npm config set prefix "$HOME/.local" --location=user

# Setup Kilocode agent harness 
# If you want to use another supported agent https://jupyter-ai.readthedocs.io/en/stable/getting-started.html#install-agents
npm install -g @kilocode/cli

# Restart the jupyter job. Then kilo should show up in the jupyter AI chat. 
# Auth chatgpt in Kilocode
kilo auth login --provider codex
# Choose the "ChatGPT Pro/Plus (browser)" option
# When you complete the auth in the browser, it will open up http://localhost:1455/auth/callback?...
# Open a new terminal, run the commands below, and then paste the entire localhost URL to complete auth
read -r -s -p "Paste callback URL: " CALLBACK_URL
echo
curl --silent --show-error --max-time 15 "$CALLBACK_URL"
unset CALLBACK_URL
```
