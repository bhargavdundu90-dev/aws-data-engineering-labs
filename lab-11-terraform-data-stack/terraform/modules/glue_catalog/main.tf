# A Glue database per lake zone. Tables are owned by the jobs that write them (Iceberg / crawler),
# so only the database and its default location live here.

resource "aws_glue_catalog_database" "this" {
  name         = var.database_name
  description  = var.description
  location_uri = var.location
  tags         = var.tags
}
