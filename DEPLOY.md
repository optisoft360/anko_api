# Auto deploy on push to `main`

This project includes:

- `deploy.sh` - runs on the server, pulls `main`, ensures the external Docker network exists, stops old containers, frees busy ports, rebuilds, and starts the new ones.
- `.github/workflows/deploy.yml` - triggers the deploy automatically after every push to `main`, and also supports manual runs from GitHub Actions.

## 1. Prepare the server

Clone the repo on the server in its final location, for example:

```bash
git clone https://github.com/ander2010/anko_api.git /home/ubuntu/anko_api
cd /home/ubuntu/anko_api
chmod +x deploy.sh
```

Make sure these are installed and working on the server:

- `git`
- `docker`
- `docker compose`

For the port safeguard, at least one of these should also be available:

- `fuser`
- `lsof`
- `ss`

Make sure your `.env` file exists in the project root on the server.

## 2. Add the SSH key

Generate a deploy key locally if you do not already have one:

```bash
ssh-keygen -t ed25519 -C "github-actions-deploy"
```

Add the public key to the server user's `~/.ssh/authorized_keys`.

## 3. Add GitHub repository secrets

In GitHub, go to:

- `Settings`
- `Secrets and variables`
- `Actions`

Create these secrets:

- `SERVER_HOST` - server IP or domain
- `SERVER_USER` - SSH user, for example `ubuntu`
- `SERVER_SSH_KEY` - private SSH key content
- `SERVER_PORT` - usually `22`
- `DEPLOY_PATH` - absolute path to the repo on the server, for example `/home/ubuntu/anko_api`
- `PORTS_TO_FREE` - optional, for example `8000` or `8000 5555`

## 4. How deployment works

On every push to `main`, GitHub Actions will:

1. SSH into the server
2. Go to `DEPLOY_PATH`
3. export the deploy settings
4. run `./deploy.sh`

The deploy script will:

1. `git fetch origin main`
2. `git reset --hard origin/main`
3. ensure Docker network `hope_default` exists
4. `docker compose down --remove-orphans`
5. free configured ports before restart
6. `docker compose up -d --build --remove-orphans`
7. `docker image prune -f`

## 5. Port safeguard

By default the script frees port `8000`, which matches this project's published API port.

If you want GitHub Actions to free more than one port, set the `PORTS_TO_FREE` secret like this:

```text
8000 5555
```

The script tries, in this order:

1. stop Docker containers exposing the port
2. kill remaining listeners with `fuser`
3. otherwise use `lsof`
4. otherwise use `ss`

## 6. Activate it

1. Push these files to GitHub.
2. On the server, clone the repo into the exact path you will store in `DEPLOY_PATH`.
3. Put the server `.env` file in that repo folder.
4. Add the SSH public key to `~/.ssh/authorized_keys` on the server.
5. Add the GitHub secrets listed above.
6. In GitHub, open `Actions` and confirm the `Deploy` workflow is visible.
7. Push a small commit to `main`.
8. Open the `Deploy` workflow run and watch the logs.

## 7. Manual deploy from GitHub

You can also open GitHub `Actions`, choose `Deploy`, and use `Run workflow`.

## 8. Manual deploy on the server

You can also run it yourself anytime:

```bash
cd /home/ubuntu/anko_api
./deploy.sh
```

## 9. Downtime note

This current setup does exactly what you asked: it stops the old containers and rebuilds the new ones.

That means there will be a short downtime during deployment.

If later you want, this can be changed to a lower-downtime deploy strategy.
