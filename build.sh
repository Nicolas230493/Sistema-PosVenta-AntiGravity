#!/usr/bin/env bash
# Script de build para Render. Sin credenciales hardcodeadas (SPEC-SEC-01 §2.2).
set -o errexit

pip install -r requirements.txt

python manage.py collectstatic --no-input
python manage.py migrate --no-input

# Grupos de roles, superusuario (desde variables de entorno) y datos base
python manage.py setup_groups
python manage.py ensure_superuser
python manage.py seed_initial_data
