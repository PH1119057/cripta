#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Запустите через sudo." >&2
  exit 1
fi

read -r -p "Bybit Testnet API key: " api_key
read -r -s -p "Bybit Testnet API secret: " api_secret
echo
if [[ -z "$api_key" || -z "$api_secret" ]]; then
  echo "Ключ и secret не могут быть пустыми." >&2
  exit 1
fi

install -d -o root -g root -m 0700 /etc/cripta/credentials
umask 077
printf '%s' "$api_key" | systemd-creds encrypt --name=bybit-testnet-api-key - /etc/cripta/credentials/bybit-testnet-api-key.cred
printf '%s' "$api_secret" | systemd-creds encrypt --name=bybit-testnet-api-secret - /etc/cripta/credentials/bybit-testnet-api-secret.cred
chmod 0600 /etc/cripta/credentials/bybit-testnet-api-key.cred /etc/cripta/credentials/bybit-testnet-api-secret.cred
unset api_key api_secret
echo "Testnet credentials зашифрованы. Обычных файлов с секретом не создано."
