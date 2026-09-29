# recupere les donnees de l'instance ec2 qui tourne chez le client
import boto3

ec2 = boto3.client('ec2')

def get_instance_data(instance_id): # fonction temporaraire qui recupere le cpu de l'instance ec2
    return "90" # le cpu ne sera pas toujours la valeur retourner, c'est une valeur de defaut, un exemple

