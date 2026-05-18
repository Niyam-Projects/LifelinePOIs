COPY (
  WITH raw AS (
    SELECT type, id, tags, geometry
    FROM '{{INPUT}}'
    WHERE (
      tags['amenity'] IN ('school', 'university', 'college', 'kindergarten')
      OR tags['building'] IN ('school', 'university', 'college')
    )
    AND (
      kind = 'node'
      OR (kind = 'area' AND (type = 'relation' OR tags['area'] = 'yes'
          OR tags['amenity'] IN ('school', 'university', 'college', 'kindergarten')
          OR tags['building'] IN ('school', 'university', 'college')))
    )
  )
  SELECT
    type,
    id,
    tags['amenity']                     AS amenity,
    tags['building']                    AS building,
    tags['isced:level']                 AS "isced:level",
    tags['isced:type']                  AS "isced:type",
    tags['grades']                      AS grades,
    tags['min_age']                     AS min_age,
    tags['max_age']                     AS max_age,
    tags['operator']                    AS operator,
    tags['operator:wikidata']           AS "operator:wikidata",
    tags['operator:type']               AS "operator:type",
    tags['name']                        AS name,
    tags['ref']                         AS ref,
    tags['phone']                       AS phone,
    tags['email']                       AS email,
    tags['website']                     AS website,
    tags['opening_hours']               AS opening_hours,
    tags['capacity']                    AS capacity,
    tags['addr:street']                 AS "addr:street",
    tags['addr:housenumber']            AS "addr:housenumber",
    tags['addr:city']                   AS "addr:city",
    tags['addr:state']                  AS "addr:state",
    tags['addr:postcode']               AS "addr:postcode",
    tags['level']                       AS level,
    tags['wheelchair']                  AS wheelchair,
    tags['fee']                         AS fee,
    tags['religion']                    AS religion,
    tags['denomination']                AS denomination,
    tags['start_date']                  AS start_date,
    tags['wikipedia']                   AS wikipedia,
    tags['wikidata']                    AS wikidata,
    tags['access']                      AS access,
    prefix_map('name:', tags)           AS names,
    {
      xmin: ST_XMin(geometry)::FLOAT,
      ymin: ST_YMin(geometry)::FLOAT,
      xmax: ST_XMax(geometry)::FLOAT,
      ymax: ST_YMax(geometry)::FLOAT
    } AS bbox,
    geometry
  FROM raw
) TO '{{OUTPUT}}' WITH (FORMAT PARQUET, COMPRESSION ZSTD);
