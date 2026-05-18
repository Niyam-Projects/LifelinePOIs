COPY (
  WITH raw AS (
    SELECT type, id, tags, geometry
    FROM '{{INPUT}}'
    WHERE (
      tags['industrial'] IN (
        'fuel', 'oil', 'oil_storage', 'refinery', 'oil_refinery',
        'gas', 'lpg_storage', 'fuel_terminal', 'petroleum'
      )
      OR tags['man_made'] IN ('petroleum_well', 'oil_well', 'gas_well')
      OR (tags['man_made'] = 'storage_tank' AND tags['content'] IN (
          'fuel', 'oil', 'gasoline', 'diesel', 'petroleum', 'gas',
          'natural_gas', 'lpg', 'lng', 'jet_fuel', 'kerosene', 'naphtha'
      ))
      OR tags['pipeline'] IN ('substation')
      OR tags['landuse'] = 'depot'
         AND tags['content'] IN ('fuel', 'oil', 'petroleum')
    )
    AND (
      kind = 'node'
      OR (kind = 'area' AND (type = 'relation' OR tags['area'] = 'yes'
          OR tags['industrial'] IS NOT NULL OR tags['man_made'] = 'storage_tank'))
      OR (kind = 'line' AND tags['man_made'] IN ('pipeline'))
    )
  )
  SELECT
    type,
    id,
    tags['industrial']              AS industrial,
    tags['man_made']                AS man_made,
    tags['landuse']                 AS landuse,
    tags['pipeline']                AS pipeline,
    tags['operator']                AS operator,
    tags['operator:wikidata']       AS "operator:wikidata",
    tags['name']                    AS name,
    tags['ref']                     AS ref,
    tags['content']                 AS content,
    tags['substance']               AS substance,
    tags['capacity']                AS capacity,
    tags['capacity:m3']             AS "capacity:m3",
    tags['diameter']                AS diameter,
    tags['location']                AS location,
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
