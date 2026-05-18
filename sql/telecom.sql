COPY (
  WITH raw AS (
    SELECT type, id, tags, geometry
    FROM '{{INPUT}}'
    WHERE (
      tags['telecom'] IS NOT NULL
      OR tags['man_made'] IN ('mast', 'antenna', 'telephone_exchange', 'communications_tower')
      OR (tags['man_made'] = 'tower' AND tags['tower:type'] IN (
          'communication', 'radio', 'radar', 'lighting', 'monitoring'
      ))
      OR tags['tower:type'] IN ('communication', 'radio', 'radar')
      OR tags['amenity'] = 'telephone'
    )
    AND (
      kind = 'node'
      OR (kind = 'area' AND (type = 'relation' OR tags['area'] = 'yes'
          OR tags['telecom'] IN ('exchange', 'data_center', 'service_node')))
      OR (kind = 'line' AND tags['telecom'] IN ('cable', 'line'))
    )
  )
  SELECT
    type,
    id,
    tags['telecom']                 AS telecom,
    tags['man_made']                AS man_made,
    tags['tower:type']              AS "tower:type",
    tags['amenity']                 AS amenity,
    tags['operator']                AS operator,
    tags['operator:wikidata']       AS "operator:wikidata",
    tags['name']                    AS name,
    tags['ref']                     AS ref,
    tags['height']                  AS height,
    tags['material']                AS material,
    tags['structure']               AS structure,
    tags['communication:mobile_phone'] AS "communication:mobile_phone",
    tags['communication:radio']     AS "communication:radio",
    tags['communication:television'] AS "communication:television",
    tags['communication:microwave'] AS "communication:microwave",
    tags['frequency']               AS frequency,
    tags['start_date']              AS start_date,
    tags['wikipedia']               AS wikipedia,
    tags['wikidata']                AS wikidata,
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
