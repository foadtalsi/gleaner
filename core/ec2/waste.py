# Computes the compute wasted per ec2 instance, and its cost

waste_data = {}  # wasted vcpu-hours per instance
prices = {}      # price of a vcpu-hour per instance, filled in by hand, later with the aws pricing api
DEFAULT_PRICE_PER_VCPU_HOUR = 0.0104  # t3.small on demand in us-east-1 ($0.0208/h for 2 vCPU), the demo's agent EC2s


def record_waste(instance_id, cpu_free_percent, vcpus, period_seconds):
    # all free CPU counts as wasted
    wasted_hours = cpu_free_percent / 100 * vcpus * period_seconds / 3600

    if instance_id not in waste_data:
        waste_data[instance_id] = 0
    waste_data[instance_id] += wasted_hours


def get_waste_report(instance_id):
    if instance_id not in waste_data:
        return None

    vcpu_hours = waste_data[instance_id]
    price = prices.get(instance_id, DEFAULT_PRICE_PER_VCPU_HOUR)
    cost = round(vcpu_hours * price, 4)

    return {
        "wasted_vcpu_hours": round(vcpu_hours, 4),
        "price_per_vcpu_hour": price,
        "wasted_cost": cost,
    }
