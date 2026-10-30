from dataclasses import dataclass, field

import lance

from flock.hub import s3


@dataclass
class DatasetConfig:
    path: str
    version: str = field(doc="")

    lazy_map: bool = field(default=True)
    lazy_download: bool = field(default=True)


class Dataset(lance.LanceDataset):
    def __init__(self, config: DatasetConfig):
        creds = s3.credentials(org="cerulean-labs")

        super().__init__(
            uri=f"s3://{config.path}/",
            version=config.version,
            storage_options=creds.storage_options(),
        )

    def __getitem__(self, idx):
        return self.__getitems__([idx])[0]

    def __getitems__(self, indices):
        """Batch data fetching with worker-safe initialization

        Args:
            indices: List[int] - batch indices to retrieve

        Returns:
            List[dict] - samples in original data format
        """
        if self._ds is None:
            # Worker-process initialization
            self._ds = lance.dataset(self.uri, **self.dataset_options)

        # Leverage native batch reading
        batch = self._ds.take(indices)

        # Convert to python-native format
        return batch.to_pylist()
