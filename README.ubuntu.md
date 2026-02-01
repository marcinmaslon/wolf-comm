# Running `wolf.py` continuously on Ubuntu

Use `systemd` to keep `wolf.py` running in the background, restart it automatically whenever it crashes, and force a restart every 12 hours.

1. **Prepare the environment**
   - Clone the repo and install dependencies using the virtual environment you already use on Windows. On Ubuntu:
     ```bash
     cd /path/to/wolf
     python -m venv .venv
     source .venv/bin/activate
     pip install -r requirements.txt
     ```
   - Copy `credentials.example.json` (if available) to `credentials.json` and fill in your Wolf credentials plus the `mqtt.url` section so the script can publish/subscribe.

2. **Create a systemd service**

   Save the following unit to `/etc/systemd/system/wolf.service` (adjust `User`, `Group`, and paths to match your system):

   ```ini
   [Unit]
   Description=Wolf SmartSet monitor
   After=network-online.target
   Wants=network-online.target

   [Service]
   Type=simple
   User=youruser
   Group=yourgroup
   WorkingDirectory=/path/to/wolf
   Environment="PATH=/path/to/wolf/.venv/bin"
   ExecStart=/path/to/wolf/.venv/bin/python wolf.py --refresh_interval 60
   Restart=on-failure
   RestartSec=10
   StandardOutput=journal
   StandardError=journal

   [Install]
   WantedBy=multi-user.target
   ```

   - `Restart=on-failure` ensures the service restarts automatically if `wolf.py` crashes.
   - You can adjust `--refresh_interval` (60s in the example) or add other CLI arguments if needed.

3. **Force a periodic restart every 12 hours**

   Create two units: `/etc/systemd/system/wolf-restart.service` to restart the main service, and `/etc/systemd/system/wolf-restart.timer` to fire it every 12 hours.

   `wolf-restart.service`:

   ```ini
   [Unit]
   Description=Restart wolf.service every 12 hours

   [Service]
   Type=oneshot
   ExecStart=/bin/systemctl restart wolf.service
   ```

   `wolf-restart.timer`:

   ```ini
   [Unit]
   Description=Timer to restart wolf.service every 12 hours

   [Timer]
   OnBootSec=5min
   OnUnitActiveSec=12h
   Persistent=true

   [Install]
   WantedBy=timers.target
   ```

   When the timer fires it starts `wolf-restart.service`, which in turn simply issues a `systemctl restart wolf.service`, so the monitor gets a fresh process even without a crash.

4. **Enable and start**
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now wolf.service
   sudo systemctl enable --now wolf-restart.timer
   ```

5. **Verify**
   - Run `systemctl status wolf.service` to make sure the service is active.
   - Check `journalctl -u wolf.service` to see logs and confirm MQTT publishes.
   - Ensure the timer is active with `systemctl list-timers wolf-restart.timer`.

6. **Stopping or debugging**
   - To stop the service or work interactively, `sudo systemctl stop wolf.service` and work directly from the repo (activating the venv first).
   - After editing `wolf.py`, rerun `sudo systemctl daemon-reload` and `sudo systemctl restart wolf.service` to pick up the changes.
