"""Retired direct PDF publication runtime."""
class PublishRejected(ValueError):
    pass

async def publish(root, args):
    raise PublishRejected('Use src.cli.publish_documents with a validated v2 chunk package.')
