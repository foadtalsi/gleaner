# manages the ec2 agents' keys so that only core can send commands to the ec2 agents
# Later, store the key in dynamodb, not hardcoded in the code

agent1_key = "agent_1_key"

AGENTS_KEY_DIR = {"agent_1": agent1_key} # list of keys known to core, it checks whether what the agent sends is in its list
