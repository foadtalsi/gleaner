# Calcule le compute gaspille par instance ec2, et son cout

waste_data = {}  # vcpu-heures gaspillees par instance
prices = {}      # prix d'un vcpu-heure par instance, a remplir a la main, plus tard avec l'api aws des prices


def record_waste(instance_id, cpu_free_percent, vcpus, period_seconds):
    # tout le CPU libre compte comme gaspille
    wasted_hours = cpu_free_percent / 100 * vcpus * period_seconds / 3600

    if instance_id not in waste_data:
        waste_data[instance_id] = 0
    waste_data[instance_id] += wasted_hours


def get_waste_report(instance_id):
    if instance_id not in waste_data:
        return None

    vcpu_hours = waste_data[instance_id]
    price = prices.get(instance_id)

    cost = None
    if price is not None:
        cost = round(vcpu_hours * price, 4)

    return {
        "wasted_vcpu_hours": round(vcpu_hours, 4),
        "price_per_vcpu_hour": price,
        "wasted_cost": cost,
    }
