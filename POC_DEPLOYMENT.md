# Proof-of-Concept (PoC) Deployment Guide

This guide is designed for a **rapid, low-cost Proof-of-Concept (PoC)** deployment. It will allow you to run the Indemnify risk engine and Oracle listener 24/7 on a cheap cloud VM (like a $5/month DigitalOcean Droplet or AWS Lightsail instance) using free public infrastructure. 

Unlike the enterprise guide, this setup cuts corners on scaling and security to prioritize speed and simplicity. We will use **Cloudflare Tunnels (cloudflared)** to securely expose your local API to the internet with automatic HTTPS, completely bypassing the need to buy a domain name or configure Nginx.

---

## Step 1: Spin Up a Cheap Cloud VM

1. Go to a cloud provider like **DigitalOcean**, **Hetzner**, or **AWS Lightsail**.
2. Create a new Virtual Machine (Droplet/Instance).
3. **OS Selection:** Choose **Ubuntu 22.04 LTS**.
4. **Size:** The cheapest tier ($5 - $10 / month) with 1 vCPU and 1GB or 2GB of RAM is perfectly fine.
5. Deploy the VM and note down its public IP address.

---

## Step 2: SSH & Install Basic Dependencies

Open your terminal and SSH into your new VM using the root account (or ubuntu account if on AWS):

```bash
ssh root@YOUR_VM_IP_ADDRESS
```

Once inside the VM, run the following commands to update the system and install Python, Git, and TMUX:

```bash
apt-get update && apt-get upgrade -y
apt-get install -y python3.11 python3.11-venv python3-pip git tmux
```

---

## Step 3: Clone the Code & Setup Environment

Download your code onto the server and install the Python dependencies.

```bash
# Clone your repository (replace with your actual repo URL)
git clone https://github.com/your-org/indemnify.git /root/indemnify
cd /root/indemnify

# Create a virtual environment and install requirements
python3 -m venv venv
source venv/bin/activate
pip install -r daemon/requirements.txt
```

Now, create your configuration file. 

```bash
nano .env
```

Paste in the following configuration. We are using the **free X Layer public RPC**:

```dotenv
# .env
ORACLE_PRIVATE_KEY=0xYOUR_ORACLE_PRIVATE_KEY_HERE
ESCROW_CONTRACT_ADDRESS=0xYOUR_PARAMETRIC_ESCROW_ADDRESS
POOL_CONTRACT_ADDRESS=0xYOUR_UNDERWRITER_POOL_ADDRESS

# Using the free public RPC for the PoC
RPC_PROVIDER_URL=https://rpc.xlayer.tech
CHAIN_ID=196

FIXED_UNDERWRITER_MARGIN=0.01
```
Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

---

## Step 4: Run the Services 24/7 using TMUX

Instead of writing complex `systemd` background services, we will use `tmux`. TMUX creates virtual terminal windows that stay running 24/7 even after you close your SSH connection.

Start a new tmux session called `indemnify`:

```bash
tmux new -s indemnify
```

Your screen will clear. You are now inside the tmux session. Let's start the API Server:

```bash
cd /root/indemnify
source venv/bin/activate
uvicorn daemon.main:app --host 127.0.0.1 --port 8000
```
*The API is now running.*

Now, we need to run the Oracle Listener at the same time. 
1. Press `Ctrl+B`, then press `C` to create a new tab in TMUX.
2. Run the oracle listener:

```bash
cd /root/indemnify
source venv/bin/activate
python -m daemon.oracle_listener
```
*The Oracle is now listening for events.*

**To leave the server running in the background:**
Press `Ctrl+B`, then press `D` (for detach). You will be returned to your normal SSH prompt, but the bots are still running! *(To go back in later, type `tmux attach -t indemnify`)*.

---

## Step 5: Expose the API to the Internet with Automatic HTTPS

OKX AI Agents require an `https://` endpoint to communicate with your skill. Setting up Nginx and SSL certificates is tedious for a PoC. Instead, we will use a **Cloudflare Tunnel**, which gives you a secure, free, HTTPS URL instantly.

Run this command to install the free Cloudflare tunneling tool:

```bash
wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
dpkg -i cloudflared-linux-amd64.deb
```

Now, tell Cloudflare to expose your local port 8000 to the internet:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

**What happens next:**
Cloudflare will print out a random URL that looks something like this:
`https://random-words-here.trycloudflare.com`

**This is your live API URL!** 
Any traffic sent to that HTTPS URL will securely tunnel directly into your cheap VM and hit your API server.

---

## Step 6: Update your OKX Agent Skill

Take the URL Cloudflare just gave you, and update your `.agents/skills/indemnify.yaml` file so the OKX OS knows where to send traffic:

```yaml
daemon:
  base_url: "https://random-words-here.trycloudflare.com"
```

## You are Done! 🚀
- Your Oracle is running 24/7 inside the tmux session.
- Your API is running 24/7 inside the tmux session.
- Cloudflare is providing free HTTPS routing.
- You are using the free OKX public RPC.

**Note on PoC limitations:**
If you ever reboot the VM, you will need to SSH back in, type `tmux new -s indemnify`, and run the commands again. The Cloudflare URL will also change every time you restart the tunnel. (If you want a permanent URL, you can buy a $2 domain and link it to your Cloudflare account later).
