"""market-hunt application package — production skeleton.

Modules migrate here from the api_server.py monolith incrementally:
  app.db          single shared Mongo layer (sync legacy + async motor target)
  app.middleware  request IDs, access logging, safe error responses
  app.routers.*   domain routers (arriving in Module 2+)
"""
