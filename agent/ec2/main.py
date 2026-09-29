import psutil

def get_cpu_usage():
    # Retourne le pourcentage d'utilisation du CPU sur une période de 1 seconde
    return psutil.cpu_percent(interval=1)


while True:
    cpu_usage = get_cpu_usage()
    print(f"Utilisation du CPU: {cpu_usage}%")