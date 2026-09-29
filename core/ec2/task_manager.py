# avec les donnees de collector.py, sa va gerer les taches qui sont en cours sur l'instance ec2 du client
import boto3
from ec2.collector import get_instance_data

task = {} # dict temporaire qui va contenir les taches a placer sur l'ec2
