"""Retired direct PDF publication runtime."""
class PublishRejected(ValueError):
    pass

async def publish(root, args):
    raise PublishRejected('Use app.cli.publish_documents with an approved package.')
