COPY (
  WITH raw AS (
    SELECT type, id, tags, geometry
    FROM '{{INPUT}}'
    WHERE (
      tags['amenity'] IN ('police', 'fire_station', 'ambulance_station')
      OR tags['emergency'] IN (
        'ambulance_station', 'fire_station', 'police',
        'disaster_response', 'ses_station', 'water_rescue', 'coast_guard',
        'lifeguard_base', 'mountain_rescue'
      )
      OR (tags['office'] = 'government' AND (
        lower(tags['name']) LIKE '%police%'
        OR lower(tags['name']) LIKE '%sheriff%'
        OR lower(tags['name']) LIKE '%constable%'
        OR lower(tags['name']) LIKE '%gendarm%'
      ))
    )
    AND (
      kind = 'node'
      OR (kind = 'area' AND (type = 'relation' OR tags['area'] = 'yes'
          OR tags['amenity'] IN ('police', 'fire_station', 'ambulance_station')
          OR tags['emergency'] IS NOT NULL))
    )
  )
  SELECT
    type,
    id,
    tags['amenity']                 AS amenity,
    tags['emergency']               AS emergency,
    tags['office']                  AS office,
    tags['operator']                AS operator,
    tags['operator:wikidata']       AS "operator:wikidata",
    tags['name']                    AS name,
    tags['ref']                     AS ref,
    tags['phone']                   AS phone,
    tags['fax']                     AS fax,
    tags['email']                   AS email,
    tags['website']                 AS website,
    tags['opening_hours']           AS opening_hours,
    tags['addr:street']             AS "addr:street",
    tags['addr:housenumber']        AS "addr:housenumber",
    tags['addr:city']               AS "addr:city",
    tags['addr:state']              AS "addr:state",
    tags['addr:postcode']           AS "addr:postcode",
    tags['fire_station:type']       AS "fire_station:type",
    tags['police']                  AS police,
    tags['level']                   AS level,
    tags['building']                AS building,
    tags['capacity']                AS capacity,
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
