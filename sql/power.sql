COPY (
  WITH raw AS (
    SELECT type, id, tags, geometry
    FROM '{{INPUT}}'
    WHERE tags['power'] IS NOT NULL
      AND (
        kind = 'node'
        OR (kind = 'line' AND (tags['area'] IS NULL OR tags['area'] != 'yes'))
        OR (kind = 'area' AND (type = 'relation' OR tags['area'] = 'yes' OR tags['power'] IN ('substation', 'plant', 'generator')))
      )
  )
  SELECT
    type,
    id,
    tags['power']                   AS power,
    tags['voltage']                 AS voltage,
    tags['operator']                AS operator,
    tags['operator:wikidata']       AS "operator:wikidata",
    tags['name']                    AS name,
    tags['ref']                     AS ref,
    tags['substation']              AS substation,
    tags['plant:source']            AS "plant:source",
    tags['plant:output:electricity'] AS "plant:output:electricity",
    tags['generator:source']        AS "generator:source",
    tags['generator:output:electricity'] AS "generator:output:electricity",
    tags['generator:type']          AS "generator:type",
    tags['cables']                  AS cables,
    tags['wires']                   AS wires,
    tags['circuits']                AS circuits,
    tags['frequency']               AS frequency,
    tags['capacity']                AS capacity,
    tags['start_date']              AS start_date,
    tags['wikipedia']               AS wikipedia,
    tags['wikidata']                AS wikidata,
    tags['height']                  AS height,
    tags['material']                AS material,
    tags['tower:type']              AS "tower:type",
    tags['location']                AS location,
    tags['access']                  AS access,
    prefix_map('name:', tags)       AS names,
    {
      xmin: ST_XMin(geometry)::FLOAT,
      ymin: ST_YMin(geometry)::FLOAT,
      xmax: ST_XMax(geometry)::FLOAT,
      ymax: ST_YMax(geometry)::FLOAT
    } AS bbox,
    geometry
  FROM raw
) TO '{{OUTPUT}}' WITH (FORMAT PARQUET, COMPRESSION ZSTD);
