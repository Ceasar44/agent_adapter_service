from agent_adapter_service.agent.config.models import CustomerServiceConfig
from agent_adapter_service.agent.contracts import AgentRecord, ConfigurationGateway


class CustomerServiceAgentFactory:
    def __init__(self, gateway: ConfigurationGateway) -> None:
        self.gateway = gateway

    async def create(self, config: CustomerServiceConfig) -> AgentRecord:
        return await self.gateway.find_or_create_agent(config)
