# Putting Switchboard on a server

A start-to-finish guide for a brand new VPS, assuming you have never set one up
before. Ubuntu 24.04.

Every command is written out in full. After most of them there's a **You should
see** line — if what you get looks different, stop there rather than carrying
on, because later steps depend on earlier ones working.

Set aside about an hour the first time.

---

## First: what you are actually building

Five separate programs, each with one job. It helps a lot to know which is
which before you start installing them.

**nginx** is the receptionist. It's the only program that listens to the public
internet. Every visitor talks to nginx, and nginx decides where to pass them.
It also hands out images and CSS directly, because there's no point waking up
Python for a file that never changes.

**Gunicorn** runs your Django code. It doesn't talk to the internet at all —
only to nginx. You run **two** copies: one for the main site, one for the short
links. They're the same code with a different set of URLs switched on.

**PostgreSQL** is the database. Accounts, links, clicks. This is the part you
can't afford to lose.

**Redis** is scratch paper. Which link goes where (so the same lookup isn't
repeated a thousand times), who's making too many requests, and a queue of
clicks waiting to be recorded. If Redis vanished, the site would get slower but
keep working.

**Celery** is the background worker. When someone opens a short link, the
redirect happens instantly and the click gets dropped in a queue. Celery picks
it up a moment later and does the slow parts — working out the country, parsing
the browser. That's why redirects stay fast.

**systemd** is the supervisor built into Linux. It starts those programs when
the server boots and restarts them if they crash. Without it you'd have to log
in and start everything by hand after every reboot.

```
   visitor
      |
   [ nginx ]  ── static files (CSS, images) ──> served directly
      |
      +──> [ Gunicorn: main site  ] ──┐
      |                               ├──> [ PostgreSQL ]
      +──> [ Gunicorn: short links] ──┘         and
                    |                      [ Redis ]
                    └─ queues a click ─> [ Celery worker ]
```

### Words that will come up

| Word | What it means here |
|---|---|
| **SSH** | Typing commands on the server from your own computer. |
| **root** | The all-powerful account. Can delete anything. Used briefly, then locked away. |
| **sudo** | "Run this one command as an administrator." |
| **package** | Installable software. `apt` is Ubuntu's installer. |
| **service** / **unit** | A program systemd looks after. |
| **socket** | A file two programs use to talk locally, instead of a network port. |
| **DNS / A record** | The setting that points a domain name at a server's IP address. |
| **domain** | This guide uses `switchboard.jasgun.me` and `sb.jasgun.me`. Substitute yours. |

### Before you start

- Your server's IP address and its root password, from your VPS provider.
- A domain you control, where you can add DNS records.
- On Windows, use PowerShell for the `ssh` commands. It's built in.

---

# Part 1 — Get onto the server

On **your own computer**, not the server:

```
ssh root@YOUR_SERVER_IP
```

Replace `YOUR_SERVER_IP` with the actual address, like `203.0.113.45`. It will
ask about authenticity the first time — type `yes`. Then enter the root
password your provider gave you.

**You should see** the prompt change to something like `root@your-server:~#`.
You're now typing commands on the server.

---

# Part 2 — Get onto your normal account

Working as `root` all the time is how servers get wrecked by one typo. You'll
run everything as `jasgun` instead — that one account logs in, owns the files
and runs the app.

**If `jasgun` already exists on this server**, skip to the test below.

**If the server only has root so far**, create it:

```
adduser jasgun
```

It asks for a password — pick a strong one and **write it down**. Press Enter
through the name and phone number questions.

Give it administrator rights:

```
usermod -aG sudo jasgun
```

Copy your SSH login access across:

```
rsync --archive --chown=jasgun:jasgun ~/.ssh /home/jasgun
```

> If that errors with "No such file or directory", you logged in with a password
> rather than an SSH key. Skip it — you'll log in with the password instead.

### Test it before you close anything

**Open a second terminal window**, leaving the root one running:

```
ssh jasgun@YOUR_SERVER_IP
```

**You should see** a prompt like `jasgun@your-server:~$`.

This matters. If you close the root window before confirming `jasgun` works and
something is wrong, you are locked out of your own server and have to rebuild
it. Only once you're in as `jasgun` should you type `exit` in the root window.

From here on, every command is as `jasgun`.

---

# Part 3 — Install the software

Update the list of available packages and upgrade what's installed:

```
sudo apt update && sudo apt upgrade -y
```

It will ask for the `jasgun` password the first time you use `sudo`. This takes
a couple of minutes. If a purple screen appears asking about restarting
services, press Tab to highlight `<Ok>` and Enter.

Now install everything at once:

```
sudo apt install -y nginx postgresql redis-server python3-venv python3-pip git ufw
```

**You should see** a lot of output ending back at your prompt with no red
errors.

Check the three main pieces are alive:

```
systemctl is-active nginx postgresql redis-server
```

**You should see** `active` three times.

---

# Part 4 — Close the doors you aren't using

A firewall so only web traffic and SSH can reach the machine.

```
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

It warns that this may disrupt SSH. You just allowed OpenSSH, so type `y`.

```
sudo ufw status
```

**You should see** `Status: active` and entries for OpenSSH and Nginx Full.

> **If you get disconnected here**, you missed `sudo ufw allow OpenSSH`. Use
> your provider's web console (every provider has one) to log in and run it.

## Lock down SSH

Servers on the public internet get password-guessing attempts within minutes of
booting — constantly, automatically, forever. A key is a file that can't be
guessed, so switching to keys and turning passwords off ends that entirely.

This matters more if your code is on a public GitHub repo, because the deploy
files name the account. A known username plus password login is a much easier
target than a known username plus a key.

### Make a key, on your own computer

Open a terminal **on your own machine**, not the server:

```
ssh-keygen -t ed25519
```

Press Enter at every prompt to accept the defaults. A passphrase is optional.

> **Already have one?** `ls ~/.ssh/id_ed25519.pub` — if that prints a filename,
> skip this and go straight to copying it.

### Copy it to the server

```
ssh-copy-id jasgun@YOUR_SERVER_IP
```

Enter the `jasgun` password when asked. On Windows, if `ssh-copy-id` isn't
found, use this instead:

```
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh jasgun@YOUR_SERVER_IP "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

### Test it — this is the step you cannot skip

Open a **new** terminal:

```
ssh jasgun@YOUR_SERVER_IP
```

**You should get straight in without being asked for a password.**

If it still asks for one, stop here and sort that out first. The next step
disables password login, and doing it while your key doesn't work locks you out
of your own server.

### Now turn passwords off

Back on the server:

```
sudo nano /etc/ssh/sshd_config
```

Find these two lines — use **Ctrl+W** to search — and set them like this,
removing any `#` at the start:

```
PermitRootLogin no
PasswordAuthentication no
```

Save with **Ctrl+O**, Enter, **Ctrl+X**. Then apply:

```
sudo systemctl restart ssh
```

**Keep your current window open.** Test from another one:

```
ssh jasgun@YOUR_SERVER_IP
```

If you get in, you're done. If not, your still-open session can undo it.

---

# Part 5 — Create the database

PostgreSQL is running, but has nothing in it yet. Create a database user:

```
sudo -u postgres createuser switchboard --pwprompt
```

It asks for a password twice. **Write this one down too** — you need it in
Part 8. It won't show anything as you type; that's normal.

Create the database itself, owned by that user:

```
sudo -u postgres createdb switchboard -O switchboard
```

**You should see** no output at all. In Unix, silence means success.

Check it exists:

```
sudo -u postgres psql -l | grep switchboard
```

**You should see** a line with `switchboard` in it.

---

# Part 6 — Make a home for the project

Websites live under `/var/www`, one folder per project. That folder belongs to
root by default, so create this project's folder and hand it to `jasgun` — then
you can work in it without typing `sudo` on every command.

```
sudo mkdir -p /var/www/switchboard
sudo chown jasgun:jasgun /var/www/switchboard
```

**You should see** no output from either. Check it worked:

```
ls -ld /var/www/switchboard
```

**You should see** a line ending in `jasgun jasgun /var/www/switchboard` — the
folder exists and you own it.

> Two separate things are now called `switchboard`: this **folder**, and the
> **database user** from Part 5. They're unrelated. The shared name is just
> tidiness.

---

# Part 7 — Put the code on the server

Download your project from GitHub:

```
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git /var/www/switchboard
```

Replace the URL with your own repository address.

Move into the backend folder — **every remaining command in this guide assumes
you are here**:

```
cd /var/www/switchboard/backend
```

Python projects keep their libraries in their own folder called a virtual
environment, so two projects can use different versions of the same library
without fighting. Create it:

```
python3 -m venv .venv
```

Install the libraries this project needs:

```
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

**You should see** a list of packages, ending with `Successfully installed`.
This takes a minute or two.

---

# Part 8 — Settings and passwords

The app reads its settings from a file called `.env`. There's an example to
copy:

```
cp .env.example .env
```

First, generate a secret key. Django uses it to sign cookies; anyone who knows
it can forge a login.

```
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(64))"
```

**Copy the long line it prints.** Run it a second time and copy that one too —
you need two different values.

Now open the settings file:

```
nano .env
```

`nano` is a simple text editor. Arrow keys move around, typing edits. There is
no mouse.

Change these lines (leave everything else as it is):

```ini
DJANGO_SECRET_KEY=<paste the first generated value>
DJANGO_DEBUG=0
ALLOWED_HOSTS=switchboard.jasgun.me,sb.jasgun.me
CSRF_TRUSTED_ORIGINS=https://switchboard.jasgun.me

DATABASE_URL=postgres://switchboard:YOUR_DB_PASSWORD@127.0.0.1:5432/switchboard

WEB_DOMAIN=https://switchboard.jasgun.me
SHORT_DOMAIN=https://sb.jasgun.me

IP_HASH_SALT=<paste the second generated value>
```

`YOUR_DB_PASSWORD` is the one from Part 5. Use your own domains.

To save: **Ctrl+O**, Enter, then **Ctrl+X** to quit.

Lock the file down so only its owner can read it — it now holds two secrets and
a database password:

```
sudo chmod 600 .env
sudo chown jasgun:jasgun .env
```

### Check the settings are sane

```
.venv/bin/python manage.py check --deploy
```

**You should see** `System check identified 1 issue` mentioning
`security.W021` about HSTS preload. **That one is expected and fine.**

If instead you see `shortener.E001`, your `DATABASE_URL` line is wrong and it's
still trying to use the local file database. Go back and fix it.

### Build the database tables

```
.venv/bin/python manage.py migrate
```

**You should see** a list of lines ending in `OK`.

### Collect the CSS and images

```
.venv/bin/python manage.py collectstatic --noinput
```

**You should see** `... static files copied`.

### Make yourself an admin account

```
.venv/bin/python manage.py createsuperuser
```

Enter an email and password. The password won't display as you type.

---

# Part 9 — Keep it running automatically

Right now nothing is running. systemd needs four instruction files, which are
already in the repository. Copy them in:

```
sudo cp /var/www/switchboard/deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Celery's scheduler needs a folder to remember when it last ran:

```
sudo mkdir -p /var/lib/switchboard
sudo chown jasgun:jasgun /var/lib/switchboard
```

Start all four, and set them to start on boot:

```
sudo systemctl enable --now switchboard-web switchboard-redirect switchboard-worker switchboard-beat
```

Check they're all alive:

```
systemctl is-active switchboard-web switchboard-redirect switchboard-worker switchboard-beat
```

**You should see** `active` four times.

> **If any says `failed`**, read why:
> ```
> sudo journalctl -u switchboard-web -n 30 --no-pager
> ```
> Swap in whichever name failed. The error is usually in the last few lines —
> most often a typo in `.env` or the wrong database password.

---

# Part 10 — Point your domain at the server

In your domain registrar or DNS provider, add two **A records**:

| Type | Name | Value |
|---|---|---|
| A | `switchboard` | your server's IP |
| A | `sb` | your server's IP |

That creates `switchboard.jasgun.me` and `sb.jasgun.me`.

DNS takes a few minutes to spread. Check from your own computer:

```
ping switchboard.jasgun.me
```

**You should see** your server's IP address. If it says unknown host, wait five
minutes and try again. **Don't continue until this works** — the HTTPS step in
Part 12 fails without it.

---

# Part 11 — Let visitors in

nginx is running but doesn't know about your site yet. Copy the two config
files in and switch them on:

```
sudo cp /var/www/switchboard/deploy/nginx/*.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/switchboard.jasgun.me.conf /etc/nginx/sites-enabled/
sudo ln -s /etc/nginx/sites-available/sb.jasgun.me.conf /etc/nginx/sites-enabled/
```

`sites-available` is everything nginx *could* serve; `sites-enabled` is what it
actually serves. The `ln -s` commands create the link between them.

Remove nginx's default welcome page, or it will answer instead of your site:

```
sudo rm -f /etc/nginx/sites-enabled/default
```

> **Using your own domain?** Rename the two files to match it, and edit the
> `server_name` line inside each one.

Check the configuration for mistakes before applying it:

```
sudo nginx -t
```

**You should see** `syntax is ok` and `test is successful`. If not, the message
names the file and line number.

Apply it:

```
sudo systemctl reload nginx
```

Now visit **http://switchboard.jasgun.me** in a browser. **You should see** the
site, unstyled or styled, but working. No padlock yet — that's next.

---

# Part 12 — Turn on HTTPS

Certbot gets a free certificate and edits your nginx config for you.

```
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d switchboard.jasgun.me -d sb.jasgun.me
```

It asks for an email (for expiry warnings), agreement to terms, and whether to
redirect HTTP to HTTPS — **choose redirect**.

**You should see** `Successfully received certificate`.

Visit **https://switchboard.jasgun.me**. **You should see** a padlock.

Certificates last 90 days and renew themselves. Confirm the timer exists:

```
systemctl list-timers | grep certbot
```

---

# Part 13 — Check everything actually works

```
curl https://switchboard.jasgun.me/health/ready
```

**You should see** `{"status": "ok", "checks": {"database": "ok", "redis": "ok"}}`.

If `redis` says `error`, Redis isn't running: `sudo systemctl start redis-server`.

Then in a browser, walk through it properly:

1. Open your site and create an account.
2. Shorten a link.
3. Open the short link — it should redirect.
4. Go back and open that link's analytics page.
5. Within a few seconds, the click should appear.

**If the redirect works but the click never shows up**, Celery isn't running:

```
sudo systemctl status switchboard-worker --no-pager
```

That's the whole deployment. The rest of this file is for later.

---

# Later: updating after you change the code

Push your changes to GitHub, then on the server:

```
cd /var/www/switchboard
git pull
cd backend
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
sudo systemctl reload switchboard-web switchboard-redirect
sudo systemctl restart switchboard-worker switchboard-beat
```

`reload` swaps the web processes without dropping anyone mid-request. Celery
needs a full `restart` to pick up changed code.

---

# Later: backups

Once real links exist this stops being optional. A short link that stops
working is a broken promise sitting on someone else's website.

```
sudo -u postgres pg_dump switchboard | gzip > ~/switchboard-$(date +%F).sql.gz
```

Then copy that file **off the server**, from your own computer:

```
scp jasgun@YOUR_SERVER_IP:~/switchboard-*.sql.gz .
```

A backup that only exists on the machine it's backing up is not a backup.

---

# Later: country data in analytics

The Country column stays empty until you add MaxMind's free database.

1. Make a free account at maxmind.com and download **GeoLite2 City** (`.mmdb`).
2. Upload it, from your own computer:
   ```
   scp GeoLite2-City.mmdb jasgun@YOUR_SERVER_IP:/tmp/
   ```
3. On the server:
   ```
   sudo mkdir -p /var/lib/geoip
   sudo mv /tmp/GeoLite2-City.mmdb /var/lib/geoip/
   sudo chown jasgun:jasgun /var/lib/geoip/GeoLite2-City.mmdb
   ```
4. Add `GEOIP_PATH=/var/lib/geoip/GeoLite2-City.mmdb` to `.env`.
5. `sudo systemctl restart switchboard-worker`

Only the Celery worker reads this file, so it's the only one to restart.

---

# Later: hosting another project on the same server

Nothing above needs redoing. nginx, PostgreSQL and Redis are shared; everything
else is kept separate per project.

For a project called `blog`:

```
sudo mkdir -p /var/www/blog
sudo chown jasgun:jasgun /var/www/blog
sudo -u postgres createuser blog --pwprompt
sudo -u postgres createdb blog -O blog
```

Then follow Parts 7 to 12 again with `blog` wherever this guide said
`switchboard`.

**One thing you must change:** Redis has 16 numbered storage areas, and each
project needs its own so one can't wipe another's data.

| Project | Cache | Celery queue |
|---|---|---|
| switchboard | 0 | 1 |
| blog | 2 | 3 |
| third project | 4 | 5 |

In the blog's `.env`, that means `REDIS_URL=redis://127.0.0.1:6379/2` and
`CELERY_BROKER_URL=redis://127.0.0.1:6379/3`.

Ports never clash because the apps talk to nginx through socket files rather
than network ports, and each project's socket lives in its own folder.

Certbot handles extra domains the same way: `sudo certbot --nginx -d blog.jasgun.me`.

### When one server isn't enough

A small VPS handles several small Django projects comfortably. Move one to its
own server when it needs a different PostgreSQL version, when one project's
memory use starts starving the others, or when you want to reboot one without
touching the rest.

---

# If something breaks

**See why a service failed:**
```
sudo journalctl -u switchboard-web -n 50 --no-pager
```
Swap in `switchboard-redirect`, `switchboard-worker` or `switchboard-beat`.

**Follow the logs live** (Ctrl+C to stop):
```
sudo journalctl -u switchboard-web -f
```

**Restart everything:**
```
sudo systemctl restart switchboard-web switchboard-redirect switchboard-worker switchboard-beat
```

**502 Bad Gateway** means nginx is up but Gunicorn isn't. Check
`switchboard-web`.

**Changed `.env`?** Restart the services — it's only read at startup.

**nginx won't reload?** Run `sudo nginx -t`; it names the file and line.
