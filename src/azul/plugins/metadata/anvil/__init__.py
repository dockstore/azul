from collections import (
    defaultdict,
)
from operator import (
    itemgetter,
)
from typing import (
    Iterable,
    Sequence,
)

from more_itertools import (
    one,
)
from more_itertools.more import (
    always_iterable,
)

from azul import (
    config,
    iif,
)
from azul.indexer.document import (
    DocumentType,
    EntityType,
    FieldPath,
    IndexName,
)
from azul.plugins import (
    DocumentSlice,
    ManifestConfig,
    MetadataPlugin,
    Sorting,
    SpecialFields,
)
from azul.plugins.metadata.anvil.bundle import (
    AnvilBundle,
)
from azul.plugins.metadata.anvil.indexer.transform import (
    ActivityTransformer,
    BaseTransformer,
    BiosampleTransformer,
    BundleTransformer,
    DatasetTransformer,
    DonorTransformer,
    FileTransformer,
)
from azul.plugins.metadata.anvil.schema import (
    anvil_schema,
)
from azul.plugins.metadata.anvil.service.aggregation import (
    AnvilAggregationStage,
    AnvilSummaryAggregationStage,
)
from azul.plugins.metadata.anvil.service.filter import (
    AnvilFilterStage,
)
from azul.plugins.metadata.anvil.service.response import (
    AnvilSearchResponseStage,
    AnvilSummaryResponseStage,
)
from azul.service.manifest_service import (
    ManifestFormat,
)
from azul.types import (
    AnyMutableJSON,
    JSON,
    MutableJSON,
    MutableJSONs,
)


class Plugin(MetadataPlugin[AnvilBundle]):

    @property
    def exposed_indices(self) -> dict[EntityType, Sorting]:
        return dict(
            activities=Sorting(field_name='activities.activity_id'),
            biosamples=Sorting(field_name='biosamples.biosample_id'),
            bundles=Sorting(field_name=self.special_fields.bundle_uuid),
            datasets=Sorting(field_name='datasets.dataset_id'),
            donors=Sorting(field_name='donors.donor_id'),
            files=Sorting(field_name='files.file_id'),
        )

    @property
    def manifest_formats(self) -> Sequence[ManifestFormat]:
        return [
            ManifestFormat.compact,
            ManifestFormat.terra_pfb,
            *iif(config.enable_replicas, [
                ManifestFormat.verbatim_jsonl,
                ManifestFormat.verbatim_pfb
            ])
        ]

    def transformer_types(self) -> Iterable[type[BaseTransformer]]:
        return (
            ActivityTransformer,
            BiosampleTransformer,
            BundleTransformer,
            DatasetTransformer,
            DonorTransformer,
            FileTransformer,
        )

    def transformers(self,
                     bundle: AnvilBundle,
                     *,
                     delete: bool
                     ) -> Iterable[BaseTransformer]:
        return [
            transformer_cls(bundle=bundle, deleted=delete)
            for transformer_cls in self.transformer_types()
        ]

    def mapping(self, index_name: IndexName) -> MutableJSON:
        mapping = super().mapping(index_name)
        if index_name.doc_type in (DocumentType.contribution, DocumentType.aggregate):
            def range_mapping(name: str, path: str) -> MutableJSON:
                return {
                    name: {
                        'path_match': path,
                        'mapping': self.range_mapping
                    }
                }

            mapping['dynamic_templates'].extend([
                range_mapping('biosample_age_range', 'contents.biosamples.donor_age_at_collection'),
                range_mapping('diagnosis_age_range', 'contents.diagnoses.diagnosis_age'),
                range_mapping('diagnosis_onset_age_range', 'contents.diagnoses.diagnosis_onset_age')
            ])
        return mapping

    @property
    def _field_mapping(self) -> MetadataPlugin._FieldMapping:
        common_fields = [
            'document_id',
            'source_datarepo_row_ids'
        ]
        return {
            'entity_id': 'entryId',
            'bundles': {
                # These field paths have a brittle coupling that must be
                # maintained to the field lookups in `self.manifest_config`.
                'uuid': self.special_fields.bundle_uuid,
                'version': self.special_fields.bundle_version
            },
            'sources': {
                # These field paths have a brittle coupling that must be
                # maintained to the field lookups in `self.manifest_config`.
                'id': self.special_fields.source_id,
                'spec': self.special_fields.source_spec
            },
            'contents': {
                'datasets': {
                    f: f'datasets.{f}' for f in [
                        *common_fields,
                        'dataset_id',
                        'consent_group',
                        'data_use_permission',
                        'owner',
                        'principal_investigator',
                        'registered_identifier',
                        'title',
                        'data_modality',
                        # This field path has a brittle coupling that must be
                        # maintained to the field lookup in
                        # `self.manifest_config`.
                        'duos_id',
                    ]
                },
                'donors': {
                    f: f'donors.{f}' for f in [
                        *common_fields,
                        'donor_id',
                        'organism_type',
                        'phenotypic_sex',
                        'reported_ethnicity',
                        'genetic_ancestry',
                    ]
                },
                'diagnoses': {
                    f: f'diagnoses.{f}' for f in [
                        *common_fields,
                        'diagnosis_id',
                        'disease',
                        'diagnosis_age_unit',
                        'diagnosis_age',
                        'onset_age_unit',
                        'onset_age',
                        'phenotype',
                        'phenopacket'
                    ]
                },
                'biosamples': {
                    f: f'biosamples.{f}' for f in [
                        *common_fields,
                        'biosample_id',
                        'anatomical_site',
                        'apriori_cell_type',
                        'biosample_type',
                        'disease',
                        'donor_age_at_collection_unit',
                        'donor_age_at_collection',
                    ]
                },
                'activities': {
                    f: f'activities.{f}' for f in [
                        *common_fields,
                        'activity_id',
                        # This field path has a brittle coupling that must be
                        # maintained to the field lookup in
                        # `self.manifest_config`.
                        'activity_table',
                        'activity_type',
                        'assay_type',
                        'data_modality',
                        'reference_assembly',
                    ]
                },
                'files': {
                    **{
                        f: f'files.{f}' for f in [
                            *common_fields,
                            'file_id',
                            'data_modality',
                            'file_format',
                            'file_size',
                            'file_md5sum',
                            'reference_assembly',
                            'file_name',
                            'is_supplementary',
                            # Not in schema
                            'crc32',
                            'sha256',
                            'drs_uri',
                        ]
                    },
                    # These field names are hard-coded in the implementation of
                    # the repository service/controller. Also, these field paths
                    # have a brittle coupling that must be maintained to the
                    # field lookups in `self.manifest_config`.
                    **{
                        # Not in schema
                        'version': 'fileVersion',
                        'uuid': 'fileId',
                    }
                }
            }
        }

    @property
    def special_fields(self) -> SpecialFields:
        return SpecialFields(source_id='source_id',
                             source_spec='source_spec',
                             bundle_uuid='bundle_uuid',
                             bundle_version='bundle_version',
                             root_entity_id='datasets.dataset_id')

    @property
    def root_entity_type(self) -> str:
        return 'datasets'

    @property
    def facets(self) -> Sequence[str]:
        return [
            *super().facets,
            'activities.activity_type',
            'activities.assay_type',
            'activities.data_modality',
            'biosamples.anatomical_site',
            'biosamples.biosample_type',
            'biosamples.disease',
            'diagnoses.disease',
            'diagnoses.phenotype',
            'diagnoses.phenopacket',
            'datasets.consent_group',
            'datasets.data_use_permission',
            'datasets.registered_identifier',
            'datasets.title',
            'donors.organism_type',
            'donors.phenotypic_sex',
            'donors.reported_ethnicity',
            'files.data_modality',
            'files.file_format',
            'files.reference_assembly',
            'files.is_supplementary',
        ]

    @property
    def manifest_config(self) -> ManifestConfig:
        result = defaultdict(dict)

        # Note that there is a brittle coupling that must be maintained between
        # the fields listed here and those used in `self._field_mapping`.
        fields_to_omit_from_manifest = [
            ('contents', 'activities', 'activity_table'),
            # We omit the `duos_id` field from manifests since there is only one
            # DUOS bundle per dataset, and that bundle only contributes to outer
            # entities of the `datasets` type, not to entities of the other
            # types, such as files, which the manifest is generated from.
            ('contents', 'datasets', 'duos_id'),
            ('contents', 'files', 'uuid'),
            ('contents', 'files', 'version'),
        ]

        # Furthermore, renamed values should match the field's path in a
        # response hit from the `/index/files` endpoint.
        fields_to_rename_in_manifest = {
            ('bundles', 'uuid'): 'bundles.bundle_uuid',
            ('bundles', 'version'): 'bundles.bundle_version',
            ('sources', 'id'): 'sources.source_id',
            ('sources', 'spec'): 'sources.source_spec',
        }

        def recurse(mapping: MetadataPlugin._FieldMapping, path: FieldPath):
            for path_element, name_or_type in mapping.items():
                new_path = (*path, path_element)
                if isinstance(name_or_type, dict):
                    recurse(name_or_type, new_path)
                elif isinstance(name_or_type, str):
                    if new_path == ('entity_id',):
                        pass
                    elif new_path in fields_to_omit_from_manifest:
                        result[path][path_element] = None
                        fields_to_omit_from_manifest.remove(new_path)
                    elif new_path in fields_to_rename_in_manifest:
                        result[path][path_element] = fields_to_rename_in_manifest.pop(new_path)
                    else:
                        result[path][path_element] = name_or_type
                else:
                    assert False, (path, path_element, name_or_type)

        recurse(self._field_mapping, ())
        assert len(fields_to_omit_from_manifest) == 0, fields_to_omit_from_manifest
        assert len(fields_to_rename_in_manifest) == 0, fields_to_rename_in_manifest
        # The file URL is synthesized from the `uuid` and `version` fields.
        # Above, we already configured these two fields to be omitted from the
        # manifest since they are not informative to the user.
        result[('contents', 'files')]['file_url'] = 'files.azul_file_url'
        return result

    primary_keys_by_table = {
        table['name']: one(table['primaryKey'])
        for table in anvil_schema['tables']
    }

    foreign_keys_by_table = {
        table['name']: [
            (r['to']['table'], r['from']['column'])
            for r in anvil_schema['relationships']
            if r['from']['table'] == table['name']
        ]
        for table in anvil_schema['tables']
    }

    def verbatim_pfb_entity_id(self, replica: JSON) -> str:
        replica_type = replica['replica_type']
        try:
            primary_key = self.primary_keys_by_table[replica_type]
        except KeyError:
            if replica_type == 'duos_dataset_registration':
                return replica['contents']['duos_id']
            else:
                return super().verbatim_pfb_entity_id(replica)
        else:
            return replica['contents'][primary_key]

    def verbatim_pfb_relations(self, replica: JSON) -> list[tuple[str, str]]:
        table_name, contents = replica['replica_type'], replica['contents']
        try:
            foreign_keys = self.foreign_keys_by_table[table_name]
        except KeyError:
            if table_name == 'duos_dataset_registration':
                return [('anvil_dataset', contents['dataset_id'])]
            else:
                return super().verbatim_pfb_relations(replica)
        else:
            return [
                (foreign_table_name, foreign_key)
                for (foreign_table_name, foreign_key_column) in foreign_keys
                # AnVIL foreign keys may be either scalars (e.g. `anvil_diagnosis.donor_id`)
                # or arrays (e.g. `anvil_activity.used_file_id`). Scalar foreign keys may be
                # null; we should never observe null values in array columns thanks to
                # BigQuery's type semantics:
                # https://cloud.google.com/bigquery/docs/reference/standard-sql/data-types#array_nulls
                for foreign_key in always_iterable(contents[foreign_key_column])
            ]

    def verbatim_pfb_links(self, replica_type: str) -> MutableJSONs:
        return (
            [
                {
                    'dst': 'anvil_dataset',
                    'name': '',
                    'multiplicity': 'ONE_TO_ONE'
                }
            ]
            if replica_type == 'duos_dataset_registration' else
            [
                {
                    'dst': r['to']['table'],
                    'name': r['name'],
                    # Each link is between a foreign key and a primary key.
                    # Primary keys are unique within their own table, but
                    # multiple rows in other tables can reference them.
                    'multiplicity': 'MANY_TO_ONE',
                }
                for r in anvil_schema['relationships']
                if r['from']['table'] == replica_type
            ]
        )

    def verbatim_pfb_schema(self, replicas: list[JSON]) -> list[JSON]:
        table_schemas_by_name = {
            schema['name']: schema
            for schema in anvil_schema['tables']
        }
        non_schema_replicas = [
            r for r in replicas
            if r['replica_type'] not in table_schemas_by_name
        ]
        # For tables not described by the AnVIL schema, fall back to building
        # their PFB schema dynamically from the shapes of the replicas
        entity_schemas = super().verbatim_pfb_schema(non_schema_replicas)
        # For the rest, use the AnVIL schema as the basis of the PFB schema
        for table_name, table_schema in table_schemas_by_name.items():
            field_schemas = [
                self._pfb_schema_from_anvil_column(table_name=table_name,
                                                   column_name='datarepo_row_id',
                                                   anvil_datatype='string',
                                                   is_optional=False)
            ]
            if table_name == 'anvil_file':
                field_schemas.append(self._pfb_schema_from_anvil_column(table_name=table_name,
                                                                        column_name='drs_uri',
                                                                        anvil_datatype='string'))
            for column_schema in table_schema['columns']:
                field_schemas.append(
                    self._pfb_schema_from_anvil_column(table_name=table_name,
                                                       column_name=column_schema['name'],
                                                       anvil_datatype=column_schema['datatype'],
                                                       is_array=column_schema['array_of'],
                                                       is_optional=not column_schema['required'])
                )

            field_schemas.sort(key=itemgetter('name'))
            entity_schemas.append({
                'name': table_name,
                'type': 'record',
                'fields': field_schemas
            })
        return entity_schemas

    def _pfb_schema_from_anvil_column(self,
                                      *,
                                      table_name: str,
                                      column_name: str,
                                      anvil_datatype: str,
                                      is_array: bool = False,
                                      is_optional: bool = True,
                                      ) -> AnyMutableJSON:
        _anvil_to_pfb_types = {
            'boolean': 'boolean',
            'float': 'double',
            'integer': 'long',
            'string': 'string',
            'fileref': 'string'
        }
        type_ = _anvil_to_pfb_types[anvil_datatype]
        if is_optional:
            type_ = ['null', type_]
        if is_array:
            type_ = {
                'type': 'array',
                'items': type_
            }
        return {
            'name': column_name,
            'namespace': table_name,
            'type': type_,
        }

    def document_slice(self, entity_type: str) -> DocumentSlice | None:
        return None

    @property
    def summary_response_stage(self) -> 'type[AnvilSummaryResponseStage]':
        return AnvilSummaryResponseStage

    @property
    def search_response_stage(self) -> 'type[AnvilSearchResponseStage]':
        return AnvilSearchResponseStage

    @property
    def summary_aggregation_stage(self) -> 'type[AnvilSummaryAggregationStage]':
        return AnvilSummaryAggregationStage

    @property
    def aggregation_stage(self) -> 'type[AnvilAggregationStage]':
        return AnvilAggregationStage

    @property
    def filter_stage(self) -> 'type[AnvilFilterStage]':
        return AnvilFilterStage
