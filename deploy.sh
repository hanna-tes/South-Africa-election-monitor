#!/bin/bash
# deploy.sh - Run this on your EC2 instance

# Update system
sudo apt update && sudo apt upgrade -y

# Install Python and pip
sudo apt install python3-pip python3-venv -y

# Create project directory
mkdir -p /home/ubuntu/ethiopia_election_monitor
cd /home/ubuntu/ethiopia_election_monitor

# Clone your repo (or upload files)
# git clone <your-repo-url> .

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Collect static files
python manage.py collectstatic --noinput

# Run migrations
python manage.py migrate

# Create superuser (optional, or do manually)
# python manage.py createsuperuser

# Run with Gunicorn on port 8504
gunicorn election_monitor.wsgi:application --bind 0.0.0.0:8504 --workers 3
