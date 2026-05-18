COPY (
  WITH raw AS (
    SELECT type, id, tags, geometry
    FROM '{{INPUT}}'
    WHERE (
      tags['man_made'] IN (
        'water_works', 'wastewater_plant', 'pumping_station',
        'water_tower', 'water_tap', 'reservoir_covered',
        'storage_tank', 'water_well', 'desalination_plant'
      )
      OR tags['landuse'] IN ('reservoir', 'basin')
      OR tags['amenity'] IN ('drinking_water')
      OR (tags['man_made'] = 'storage_tank' AND tags['content'] IN ('water', 'drinking_water'))
    )
    AND (
      kind = 'node'
      OR (kind = 'area' AND (type = 'relation' OR tags['area'] = 'yes'
          OR tags['man_made'] IN ('water_works', 'wastewater_plant', 'reservoir_covered', 'storage_tank', 'desalination_plant')))
      OR (kind = 'line' AND tags['man_made'] IN ('pipeline'))
    )
  )
  SELECT
    type,
    id,
    tags['man_made']                AS man_made,
    tags['amenity']                 AS amenity,
    tags['landuse']                 AS landuse,
    tags['operator']                AS operator,
    tags['operator:wikidata']       AS "operator:wikidata",
    tags['name']                    AS name,
    tags['ref']                     AS ref,
    tags['capacity']                AS capacity,
    tags['capacity:persons']        AS "capacity:persons",
    tags['content']                 AS content,
    tags['drinking_water']          AS drinking_water,
    tags['pump']                    AS pump,
    tags['pipeline']                AS pipeline,
    tags['substance']               AS substance,
    tags['diameter']                AS diameter,
    tags['depth']                   AS depth,
    tags['height']                  AS height,
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
