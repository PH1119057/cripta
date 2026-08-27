#!/usr/bin/env bash
set -euo pipefail

backup_root=/data/cripta/backups/system
stamp=$(date -u +%Y%m%dT%H%M%SZ)
final_dir="$backup_root/$stamp"
work_dir="$backup_root/.${stamp}.tmp"

install -d -o root -g root -m 0700 "$backup_root" "$work_dir"
cleanup() { rm -rf -- "$work_dir"; }
trap cleanup EXIT

sudo -u postgres pg_dump --format=custom cripta > "$work_dir/cripta.pgdump"
tar --create --gzip --file="$work_dir/project.tar.gz" \
  /srv/cripta /srv/cripta-share/reports
tar --create --gzip --file="$work_dir/configuration.tar.gz" --ignore-failed-read \
  /etc/systemd/system/cripta-dashboard.service \
  /etc/systemd/system/cripta-download-expansion.service \
  /etc/systemd/system/cripta-job-intake.service \
  /etc/systemd/system/cripta-job-runner.service \
  /etc/systemd/system/cripta-bybit-latency.service \
  /etc/systemd/system/cripta-safety-observer.service \
  /etc/systemd/system/cripta-private-runtime.service \
  /etc/systemd/system/cripta-health-monitor.service \
  /etc/systemd/system/cripta-opportunity-tracker.service \
  /etc/systemd/system/cripta-dataset-manifest.service \
  /etc/systemd/system/cripta-backup.service \
  /etc/systemd/system/cripta-backup.timer \
  /etc/nginx/sites-available/cripta-dashboard \
  /etc/nginx/cripta-dashboard.htpasswd \
  /etc/ssl/certs/cripta-dashboard.crt \
  /etc/ssl/private/cripta-dashboard.key \
  /etc/cripta/credentials/bybit-mainnet.cred \
  /etc/fstab

(
  cd "$work_dir"
  sha256sum cripta.pgdump project.tar.gz configuration.tar.gz > SHA256SUMS
  printf '{"created_at_utc":"%s","database":"cripta","includes_heavy_datasets":false}\n' "$stamp" > manifest.json
  sha256sum --check SHA256SUMS >/dev/null
  pg_restore --list cripta.pgdump >/dev/null
  tar -tzf project.tar.gz >/dev/null
  tar -tzf configuration.tar.gz >/dev/null
)
chmod -R go-rwx "$work_dir"
mv -- "$work_dir" "$final_dir"
trap - EXIT
install -d -o root -g cripta -m 0750 /var/lib/cripta/backup
printf '{"state":"verified","created_at_utc":"%s","path":"%s","includes_heavy_datasets":false}\n' "$stamp" "$final_dir" > /var/lib/cripta/backup/latest.json.tmp
chown root:cripta /var/lib/cripta/backup/latest.json.tmp
chmod 0640 /var/lib/cripta/backup/latest.json.tmp
mv /var/lib/cripta/backup/latest.json.tmp /var/lib/cripta/backup/latest.json

# Local operational retention. External/off-site retention is a separate task.
find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name '20????????T??????Z' -mtime +14 -exec rm -rf -- {} +
